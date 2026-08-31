import unittest
from unittest.mock import patch, MagicMock
import numpy as np
from pdf2zh.doclayout import (
    OnnxModel,
    YoloResult,
    YoloBox,
    select_layout_predictions,
)


class TestLayoutPredictionSelection(unittest.TestCase):
    def test_supported_low_confidence_table_footnote_is_retained(self):
        predictions = np.array(
            [
                [0, 0, 100, 100, 0.9, 0],
                # A tighter table-body detection ends immediately before the
                # multi-line note, while the outer table container includes both.
                [0, 0, 100, 60, 0.8, 0],
                [2, 61, 98, 82, 0.031, 1],
            ],
            dtype=np.float32,
        )

        selected = select_layout_predictions(
            predictions,
            {0: "table", 1: "table_footnote"},
        )

        self.assertEqual(selected.shape[0], 3)

    def test_low_confidence_bottom_table_row_is_not_retained_as_footnote(self):
        predictions = np.array(
            [
                [0, 0, 100, 100, 0.9, 0],
                # This has the old bottom-of-table geometry, but it overlaps the
                # table rather than sitting below a separately detected body.
                [2, 70, 98, 90, 0.031, 1],
            ],
            dtype=np.float32,
        )

        selected = select_layout_predictions(
            predictions,
            {0: "table", 1: "table_footnote"},
        )

        self.assertEqual(selected.shape[0], 1)

    def test_low_confidence_candidate_must_be_a_horizontal_text_band(self):
        predictions = np.array(
            [
                [0, 0, 100, 60, 0.9, 0],
                [30, 61, 70, 81, 0.04, 1],
            ],
            dtype=np.float32,
        )

        selected = select_layout_predictions(
            predictions,
            {0: "table", 1: "table_footnote"},
        )

        self.assertEqual(selected.shape[0], 1)

    def test_unsupported_low_confidence_candidates_are_rejected(self):
        predictions = np.array(
            [
                [0, 0, 100, 100, 0.9, 0],
                [5, 10, 95, 30, 0.04, 1],
                [120, 70, 190, 95, 0.04, 1],
                [5, 70, 95, 96, 0.04, 2],
            ],
            dtype=np.float32,
        )

        selected = select_layout_predictions(
            predictions,
            {0: "table", 1: "table_footnote", 2: "plain text"},
        )

        self.assertEqual(selected.shape[0], 1)

    def test_low_confidence_table_footnote_must_touch_table_bottom(self):
        predictions = np.array(
            [
                [0, 0, 100, 60, 0.9, 0],
                [2, 75, 98, 95, 0.04, 1],
            ],
            dtype=np.float32,
        )

        selected = select_layout_predictions(
            predictions,
            {0: "table", 1: "table_footnote"},
        )

        self.assertEqual(selected.shape[0], 1)


class TestOnnxModel(unittest.TestCase):
    @patch("onnx.load")
    @patch("onnxruntime.InferenceSession")
    def setUp(self, mock_inference_session, mock_onnx_load):
        # Mock ONNX model metadata
        mock_model = MagicMock()
        mock_model.metadata_props = [
            MagicMock(key="stride", value="32"),
            MagicMock(key="names", value="['class1', 'class2']"),
        ]
        mock_onnx_load.return_value = mock_model

        # Initialize OnnxModel with a fake path
        self.model_path = "fake_model_path.onnx"
        self.model = OnnxModel(self.model_path)

    def test_stride_property(self):
        # Test that stride is correctly set from model metadata
        self.assertEqual(self.model.stride, 32)

    def test_resize_and_pad_image(self):
        # Create a dummy image (100x200)
        image = np.ones((100, 200, 3), dtype=np.uint8)
        resized_image = self.model.resize_and_pad_image(image, 1024)

        # Validate the output shape
        self.assertEqual(resized_image.shape[0], 512)
        self.assertEqual(resized_image.shape[1], 1024)

        # Check that padding has been added
        padded_height = resized_image.shape[0] - image.shape[0]
        padded_width = resized_image.shape[1] - image.shape[1]
        self.assertGreater(padded_height, 0)
        self.assertGreater(padded_width, 0)

    def test_scale_boxes(self):
        img1_shape = (1024, 1024)  # Model input shape
        img0_shape = (500, 300)  # Original image shape
        boxes = np.array([[512, 512, 768, 768]])  # Example bounding box

        scaled_boxes = self.model.scale_boxes(img1_shape, boxes, img0_shape)

        # Verify the output is scaled correctly
        self.assertEqual(scaled_boxes.shape, boxes.shape)
        self.assertTrue(np.all(scaled_boxes <= max(img0_shape)))

    def test_predict(self):
        # Mock model inference output
        mock_output = np.random.random((1, 300, 6))
        self.model.model.run.return_value = [mock_output]

        # Create a dummy image
        image = np.ones((500, 300, 3), dtype=np.uint8)

        results = self.model.predict(image)

        # Validate predictions
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], YoloResult)
        self.assertGreater(len(results[0].boxes), 0)
        self.assertIsInstance(results[0].boxes[0], YoloBox)


class TestYoloResult(unittest.TestCase):
    def test_yolo_result(self):
        # Example prediction data
        boxes = [
            [100, 200, 300, 400, 0.9, 0],
            [50, 100, 150, 200, 0.8, 1],
        ]
        names = ["class1", "class2"]

        result = YoloResult(boxes, names)

        # Validate the number of boxes and their order by confidence
        self.assertEqual(len(result.boxes), 2)
        self.assertGreater(result.boxes[0].conf, result.boxes[1].conf)
        self.assertEqual(result.names, names)


class TestYoloBox(unittest.TestCase):
    def test_yolo_box(self):
        # Example box data
        box_data = [100, 200, 300, 400, 0.9, 0]

        box = YoloBox(box_data)

        # Validate box properties
        self.assertEqual(box.xyxy, box_data[:4])
        self.assertEqual(box.conf, box_data[4])
        self.assertEqual(box.cls, box_data[5])


if __name__ == "__main__":
    unittest.main()
