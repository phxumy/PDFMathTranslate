import abc
import os.path

import cv2
import numpy as np
import ast
from babeldoc.assets.assets import get_doclayout_onnx_model_path

try:
    import onnx
    import onnxruntime
except ImportError as e:
    if "DLL load failed" in str(e):
        raise OSError(
            "Microsoft Visual C++ Redistributable is not installed. "
            "Download it at https://aka.ms/vs/17/release/vc_redist.x64.exe"
        ) from e
    raise

from huggingface_hub import hf_hub_download

from pdf2zh.config import ConfigManager


_LAYOUT_CONFIDENCE_THRESHOLD = 0.25
_SUPPLEMENTAL_TABLE_FOOTNOTE_THRESHOLD = 0.025


def _horizontal_intersection_over_candidate(
    candidate: np.ndarray,
    table: np.ndarray,
) -> float:
    candidate_width = max(0.0, float(candidate[2] - candidate[0]))
    if candidate_width <= 0:
        return 0.0
    intersection = max(
        0.0,
        min(float(candidate[2]), float(table[2]))
        - max(float(candidate[0]), float(table[0])),
    )
    return intersection / candidate_width


def _is_supported_table_footnote(
    candidate: np.ndarray,
    tables: np.ndarray,
) -> bool:
    """Accept only a thin text band immediately below a detected table body.

    Table detectors commonly emit both an outer table container and a tighter
    box around the tabular rows.  A genuine note can be inside the outer box but
    must still lie almost entirely below the tighter body box.  Merely occupying
    the bottom of a table is not sufficient: an ordinary final row has substantial
    vertical overlap with every supporting table box and therefore fails closed.
    """

    candidate_width = max(0.0, float(candidate[2] - candidate[0]))
    candidate_height = max(0.0, float(candidate[3] - candidate[1]))
    if candidate_width <= 0 or candidate_height <= 0:
        return False
    # A table note is a horizontal prose band, not a compact cell or a tall block.
    if candidate_width / candidate_height < 4.0:
        return False

    for table in tables:
        table_width = max(0.0, float(table[2] - table[0]))
        table_height = max(0.0, float(table[3] - table[1]))
        if table_width <= 0 or table_height <= 0:
            continue
        vertical_overlap = max(
            0.0,
            min(float(candidate[3]), float(table[3]))
            - max(float(candidate[1]), float(table[1])),
        )
        vertical_gap = float(candidate[1]) - float(table[3])
        if (
            _horizontal_intersection_over_candidate(candidate, table) >= 0.92
            and candidate_width / table_width >= 0.60
            and candidate_width / table_width <= 1.12
            and vertical_overlap <= 0.20 * candidate_height
            and vertical_gap >= -0.20 * candidate_height
            and vertical_gap <= max(0.08 * table_height, 0.15 * candidate_height)
        ):
            return True
    return False


def select_layout_predictions(
    predictions: np.ndarray,
    names: dict[int, str] | list[str],
) -> np.ndarray:
    """Retain strong detections plus strictly supported table-footnote candidates."""
    if predictions.size == 0:
        return predictions
    strong_mask = predictions[..., 4] > _LAYOUT_CONFIDENCE_THRESHOLD
    strong = predictions[strong_mask]
    table_class_ids = {
        int(class_id)
        for class_id, name in (
            names.items() if isinstance(names, dict) else enumerate(names)
        )
        if name == "table"
    }
    footnote_class_ids = {
        int(class_id)
        for class_id, name in (
            names.items() if isinstance(names, dict) else enumerate(names)
        )
        if name == "table_footnote"
    }
    if not table_class_ids or not footnote_class_ids:
        return strong
    tables = np.asarray([row[:4] for row in strong if int(row[-1]) in table_class_ids])
    if tables.size == 0:
        return strong
    supplemental = [
        row
        for row in predictions[~strong_mask]
        if float(row[4]) > _SUPPLEMENTAL_TABLE_FOOTNOTE_THRESHOLD
        and int(row[-1]) in footnote_class_ids
        and _is_supported_table_footnote(row[:4], tables)
    ]
    if not supplemental:
        return strong
    return np.concatenate((strong, np.asarray(supplemental)), axis=0)


class DocLayoutModel(abc.ABC):
    @staticmethod
    def load_onnx():
        model = OnnxModel.from_pretrained()
        return model

    @staticmethod
    def load_available():
        return DocLayoutModel.load_onnx()

    @property
    @abc.abstractmethod
    def stride(self) -> int:
        """Stride of the model input."""
        pass

    @abc.abstractmethod
    def predict(self, image, imgsz=1024, **kwargs) -> list:
        """
        Predict the layout of a document page.

        Args:
            image: The image of the document page.
            imgsz: Resize the image to this size. Must be a multiple of the stride.
            **kwargs: Additional arguments.
        """
        pass


class YoloResult:
    """Helper class to store detection results from ONNX model."""

    def __init__(self, boxes, names):
        self.boxes = [YoloBox(data=d) for d in boxes]
        self.boxes.sort(key=lambda x: x.conf, reverse=True)
        self.names = names


class YoloBox:
    """Helper class to store detection results from ONNX model."""

    def __init__(self, data):
        self.xyxy = data[:4]
        self.conf = data[-2]
        self.cls = data[-1]


class OnnxModel(DocLayoutModel):
    def __init__(self, model_path: str):
        self.model_path = model_path

        model = onnx.load(model_path)
        metadata = {d.key: d.value for d in model.metadata_props}
        self._stride = ast.literal_eval(metadata["stride"])
        self._names = ast.literal_eval(metadata["names"])

        self.model = onnxruntime.InferenceSession(model.SerializeToString())

    @staticmethod
    def from_pretrained():
        pth = get_doclayout_onnx_model_path()
        return OnnxModel(pth)

    @property
    def stride(self):
        return self._stride

    def resize_and_pad_image(self, image, new_shape):
        """
        Resize and pad the image to the specified size, ensuring dimensions are multiples of stride.

        Parameters:
        - image: Input image
        - new_shape: Target size (integer or (height, width) tuple)
        - stride: Padding alignment stride, default 32

        Returns:
        - Processed image
        """
        if isinstance(new_shape, int):
            new_shape = (new_shape, new_shape)

        h, w = image.shape[:2]
        new_h, new_w = new_shape

        # Calculate scaling ratio
        r = min(new_h / h, new_w / w)
        resized_h, resized_w = int(round(h * r)), int(round(w * r))

        # Resize image
        image = cv2.resize(
            image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR
        )

        # Calculate padding size and align to stride multiple
        pad_w = (new_w - resized_w) % self.stride
        pad_h = (new_h - resized_h) % self.stride
        top, bottom = pad_h // 2, pad_h - pad_h // 2
        left, right = pad_w // 2, pad_w - pad_w // 2

        # Add padding
        image = cv2.copyMakeBorder(
            image, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )

        return image

    def scale_boxes(self, img1_shape, boxes, img0_shape):
        """
        Rescales bounding boxes (in the format of xyxy by default) from the shape of the image they were originally
        specified in (img1_shape) to the shape of a different image (img0_shape).

        Args:
            img1_shape (tuple): The shape of the image that the bounding boxes are for,
                in the format of (height, width).
            boxes (torch.Tensor): the bounding boxes of the objects in the image, in the format of (x1, y1, x2, y2)
            img0_shape (tuple): the shape of the target image, in the format of (height, width).

        Returns:
            boxes (torch.Tensor): The scaled bounding boxes, in the format of (x1, y1, x2, y2)
        """

        # Calculate scaling ratio
        gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])

        # Calculate padding size
        pad_x = round((img1_shape[1] - img0_shape[1] * gain) / 2 - 0.1)
        pad_y = round((img1_shape[0] - img0_shape[0] * gain) / 2 - 0.1)

        # Remove padding and scale boxes
        boxes[..., :4] = (boxes[..., :4] - [pad_x, pad_y, pad_x, pad_y]) / gain
        return boxes

    def predict(self, image, imgsz=1024, **kwargs):
        # Preprocess input image
        orig_h, orig_w = image.shape[:2]
        pix = self.resize_and_pad_image(image, new_shape=imgsz)
        pix = np.transpose(pix, (2, 0, 1))  # CHW
        pix = np.expand_dims(pix, axis=0)  # BCHW
        pix = pix.astype(np.float32) / 255.0  # Normalize to [0, 1]
        new_h, new_w = pix.shape[2:]

        # Run inference
        preds = self.model.run(None, {"images": pix})[0]

        # Postprocess predictions
        preds = select_layout_predictions(preds, self._names)
        preds[..., :4] = self.scale_boxes(
            (new_h, new_w), preds[..., :4], (orig_h, orig_w)
        )
        return [YoloResult(boxes=preds, names=self._names)]


class ModelInstance:
    value: OnnxModel = None
