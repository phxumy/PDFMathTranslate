import logging
from asyncio import CancelledError
from typing import Any, Dict, Optional, Sequence, Tuple, cast
import numpy as np

from pdfminer import settings
from pdfminer.pdfcolor import PREDEFINED_COLORSPACE, PDFColorSpace
from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfinterp import (
    PDFPageInterpreter,
    PDFResourceManager,
    PDFContentParser,
    PDFInterpreterError,
    Color,
    PDFStackT,
    LITERAL_FORM,
    LITERAL_IMAGE,
)
from pdfminer.pdffont import PDFFont
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import (
    PDFObjRef,
    dict_value,
    list_value,
    resolve1,
    stream_value,
)
from pdfminer.psexceptions import PSEOF
from pdfminer.psparser import (
    PSKeyword,
    PSLiteral,
    keyword_name,
    literal_name,
)
from pdfminer.utils import (
    MATRIX_IDENTITY,
    Matrix,
    Rect,
    mult_matrix,
    apply_matrix_pt,
)

from pdf2zh.codex_errors import CodexPermanentError
from pdf2zh.source_font import repair_embedded_cff_encoding
from pdf2zh.translator import TranslationAbortedError

log = logging.getLogger(__name__)


def safe_float(o: Any) -> Optional[float]:
    try:
        return float(o)
    except (TypeError, ValueError):
        return None


def _serialize_pdf_operand(value: Any) -> str:
    """Emit PDF arrays with PDF separators instead of Python list commas."""
    if isinstance(value, PSLiteral):
        encoded = "".join(
            (
                chr(byte)
                if 33 <= byte <= 126 and chr(byte) not in "()<>[]{}/%#"
                else f"#{byte:02X}"
            )
            for byte in literal_name(value).encode("utf-8")
        )
        return "/" + encoded
    if isinstance(value, (list, tuple)):
        return "[" + " ".join(_serialize_pdf_operand(item) for item in value) + "]"
    if isinstance(value, float):
        return f"{value:f}"
    return str(value).replace("'", "")


class PDFPageInterpreterEx(PDFPageInterpreter):
    """Processor for the content of a PDF page

    Reference: PDF Reference, Appendix A, Operator Summary
    """

    def __init__(
        self, rsrcmgr: PDFResourceManager, device: PDFDevice, obj_patch
    ) -> None:
        self.rsrcmgr = rsrcmgr
        self.device = device
        self.obj_patch = obj_patch

    def dup(self) -> "PDFPageInterpreterEx":
        return self.__class__(self.rsrcmgr, self.device, self.obj_patch)

    def get_current_state(self):
        # pdfminer saves color values but leaves the active color spaces out of
        # q/Q. Losing them changes how many SC(N)/sc(n) operands we consume,
        # corrupting subsequent source paths after a nested grayscale glyph.
        return (*super().get_current_state(), self.scs, self.ncs)

    def set_current_state(self, state):
        super().set_current_state(state[:3])
        self.scs, self.ncs = state[3:]

    def init_resources(self, resources: Dict[object, object]) -> None:
        # 重载设置 fontid 和 descent
        """Prepare the fonts and XObjects listed in the Resource attribute."""
        self.resources = resources
        self.fontmap: Dict[object, PDFFont] = {}
        self.fontid: Dict[PDFFont, object] = {}
        self.xobjmap = {}
        self.csmap: Dict[str, PDFColorSpace] = PREDEFINED_COLORSPACE.copy()
        if not resources:
            return

        def get_colorspace(spec: object) -> Optional[PDFColorSpace]:
            if isinstance(spec, list):
                name = literal_name(spec[0])
            else:
                name = literal_name(spec)
            if name == "ICCBased" and isinstance(spec, list) and len(spec) >= 2:
                return PDFColorSpace(name, stream_value(spec[1])["N"])
            elif name == "DeviceN" and isinstance(spec, list) and len(spec) >= 2:
                return PDFColorSpace(name, len(list_value(spec[1])))
            else:
                return PREDEFINED_COLORSPACE.get(name)

        for k, v in dict_value(resources).items():
            # log.debug("Resource: %r: %r", k, v)
            if k == "Font":
                for fontid, spec in dict_value(v).items():
                    objid = None
                    if isinstance(spec, PDFObjRef):
                        objid = spec.objid
                    spec = dict_value(spec)
                    self.fontmap[fontid] = self.rsrcmgr.get_font(objid, spec)
                    repair_embedded_cff_encoding(self.fontmap[fontid], spec)
                    self.fontmap[fontid].descent = 0  # hack fix descent
                    self.fontid[self.fontmap[fontid]] = fontid
            elif k == "ColorSpace":
                for csid, spec in dict_value(v).items():
                    colorspace = get_colorspace(resolve1(spec))
                    if colorspace is not None:
                        if colorspace.name in ("ICCBased", "DeviceN"):
                            # Keep the original resource and profile for source
                            # glyphs; converting its components to RGB changes
                            # colors when the profile is not device RGB.
                            colorspace._pdf2zh_resource_name = csid
                        self.csmap[csid] = colorspace
            elif k == "ProcSet":
                self.rsrcmgr.get_procset(list_value(v))
            elif k == "XObject":
                for xobjid, xobjstrm in dict_value(v).items():
                    self.xobjmap[xobjid] = xobjstrm
        # Figures are finalized while page contents are still being interpreted.
        # Publish the current page's resource maps immediately so both immediate
        # figure rendering and deferred page rendering use the correct fonts.
        self.device.fontid = self.fontid
        self.device.fontmap = self.fontmap

    def do_S(self) -> None:
        # 重载过滤非公式线条
        """Stroke path"""

        def is_black(color: Color) -> bool:
            if color is None:
                # PDF's initial stroke color is black; pdfminer represents an
                # untouched default as None. Publisher fractions often rely on it.
                return self.scs is None or self.scs.name == "DeviceGray"
            if isinstance(color, (tuple, list)):
                if len(color) == 4:
                    # The all-zero CMYK color is white, not a formula rule.
                    return tuple(color) == (0, 0, 0, 1)
                return bool(color) and all(component == 0 for component in color)
            else:
                return color == 0

        self._pending_uri_underline_marker = ""
        preserve = getattr(self.device, "preserve_source_stroke", None)
        if (
            callable(preserve)
            and len(self.curpath) == 2
            and self.curpath[0][0] == "m"
            and self.curpath[1][0] == "l"
            and preserve(
                apply_matrix_pt(self.ctm, self.curpath[0][-2:]),
                apply_matrix_pt(self.ctm, self.curpath[1][-2:]),
            )
        ):
            self.curpath = []
            return
        marker = getattr(self.device, "mark_uri_underline", None)
        if (
            callable(marker)
            and len(self.curpath) == 2
            and self.curpath[0][0] == "m"
            and self.curpath[1][0] == "l"
        ):
            self._pending_uri_underline_marker = marker(
                apply_matrix_pt(self.ctm, self.curpath[0][-2:]),
                apply_matrix_pt(self.ctm, self.curpath[1][-2:]),
                self.graphicstate,
            )
            if self._pending_uri_underline_marker:
                self.curpath = []
                return

        if (
            len(self.curpath) == 2
            and self.curpath[0][0] == "m"
            and self.curpath[1][0] == "l"
            and apply_matrix_pt(self.ctm, self.curpath[0][-2:])[1]
            == apply_matrix_pt(self.ctm, self.curpath[1][-2:])[1]
            and is_black(self.graphicstate.scolor)
            # Dashed diagram lines must stay in the original graphics stream;
            # the movable formula-line renderer only represents solid rules.
            and not (self.graphicstate.dash and self.graphicstate.dash[0])
        ):  # 独立直线，水平，黑色
            # print(apply_matrix_pt(self.ctm,self.curpath[0][-2:]),apply_matrix_pt(self.ctm,self.curpath[1][-2:]),self.graphicstate.scolor)
            self.device.paint_path(self.graphicstate, True, False, False, self.curpath)
            self.curpath = []
            return "n"
        else:
            self.curpath = []

    ############################################################
    # 重载过滤非公式线条（F/B）
    def do_f(self) -> None:
        """Fill path using nonzero winding number rule"""
        # self.device.paint_path(self.graphicstate, False, True, False, self.curpath)
        self.curpath = []

    def do_F(self) -> None:
        """Fill path using nonzero winding number rule (obsolete)"""

    def do_f_a(self) -> None:
        """Fill path using even-odd rule"""
        # self.device.paint_path(self.graphicstate, False, True, True, self.curpath)
        self.curpath = []

    def do_B(self) -> None:
        """Fill and stroke path using nonzero winding number rule"""
        # self.device.paint_path(self.graphicstate, True, True, False, self.curpath)
        self.curpath = []

    def do_B_a(self) -> None:
        """Fill and stroke path using even-odd rule"""
        # self.device.paint_path(self.graphicstate, True, True, True, self.curpath)
        self.curpath = []

    ############################################################
    # 重载返回调用参数（SCN）
    def do_SCN(self) -> None:
        """Set color for stroking operations."""
        if self.scs:
            n = self.scs.ncomponents
        else:
            if settings.STRICT:
                raise PDFInterpreterError("No colorspace specified!")
            n = 1
        args = self.pop(n)
        self.graphicstate.scolor = cast(Color, args)
        return args

    def do_scn(self) -> None:
        """Set color for nonstroking operations"""
        if self.ncs:
            n = self.ncs.ncomponents
        else:
            if settings.STRICT:
                raise PDFInterpreterError("No colorspace specified!")
            n = 1
        args = self.pop(n)
        self.graphicstate.ncolor = cast(Color, args)
        return args

    def do_SC(self) -> None:
        """Set color for stroking operations"""
        return self.do_SCN()

    def do_sc(self) -> None:
        """Set color for nonstroking operations"""
        return self.do_scn()

    def do_Do(self, xobjid_arg: PDFStackT) -> None:
        # 重载设置 xobj 的 obj_patch
        """Invoke named XObject"""
        xobjid = literal_name(xobjid_arg)
        try:
            xobj = stream_value(self.xobjmap[xobjid])
        except KeyError:
            if settings.STRICT:
                raise PDFInterpreterError("Undefined xobject id: %r" % xobjid)
            return
        # log.debug("Processing xobj: %r", xobj)
        subtype = xobj.get("Subtype")
        if subtype is LITERAL_FORM and "BBox" in xobj:
            interpreter = self.dup()
            bbox = cast(Rect, list_value(xobj["BBox"]))
            matrix = cast(Matrix, list_value(xobj.get("Matrix", MATRIX_IDENTITY)))
            # According to PDF reference 1.7 section 4.9.1, XObjects in
            # earlier PDFs (prior to v1.2) use the page's Resources entry
            # instead of having their own Resources entry.
            xobjres = xobj.get("Resources")
            if xobjres:
                resources = dict_value(xobjres)
            else:
                resources = self.resources.copy()
            self.device.begin_figure(xobjid, bbox, matrix)
            ctm = mult_matrix(matrix, self.ctm)
            ops_base = interpreter.render_contents(
                resources,
                [xobj],
                ctm=ctm,
                initial_state=self.get_current_state(),
            )
            # Form invocation implicitly saves and restores the caller's
            # graphics state. Its final color spaces must not leak outward.
            try:  # 有的时候 form 字体加不上这里会烂掉
                self.device.fontid = interpreter.fontid
                self.device.fontmap = interpreter.fontmap
                ops_new = self.device.end_figure(xobjid)
                ctm_inv = np.linalg.inv(np.array(ctm[:4]).reshape(2, 2))
                np_version = np.__version__
                if np_version.split(".")[0] >= "2":
                    pos_inv = -np.asmatrix(ctm[4:]) * ctm_inv
                else:
                    pos_inv = -np.mat(ctm[4:]) * ctm_inv
                a, b, c, d = ctm_inv.reshape(4).tolist()
                e, f = pos_inv.tolist()[0]
                self.obj_patch[self.xobjmap[xobjid].objid] = (
                    f"q {ops_base}Q {a} {b} {c} {d} {e} {f} cm {ops_new}"
                )
            except (CancelledError, TranslationAbortedError, CodexPermanentError):
                # end_figure translates text inside Form XObjects, including
                # journal mastheads. A pending browser request must stop the
                # page here, before later regions can hide the original error
                # behind TASK_PENDING_BATCH or produce an incomplete page.
                raise
            except Exception:
                pass
        elif subtype is LITERAL_IMAGE and "Width" in xobj and "Height" in xobj:
            if self._extract_fraction_image_rule(xobj):
                return "n"
            self.device.begin_figure(xobjid, (0, 0, 1, 1), MATRIX_IDENTITY)
            self.device.render_image(xobjid, xobj)
            self.device.end_figure(xobjid)
        else:
            # unsupported xobject type.
            pass

    def _extract_fraction_image_rule(self, xobj) -> bool:
        """Move a solid one-pixel stencil fraction rule with nearby mathematics."""
        if not (
            xobj.get("ImageMask")
            and xobj.get("Width") == xobj.get("Height") == 1
            and xobj.get("BitsPerComponent", 1) == 1
        ):
            return False
        a, b, c, d, _, _ = self.ctm
        width, height = abs(a), abs(d)
        if (
            abs(b) > 1e-6
            or abs(c) > 1e-6
            or not (1 <= width <= 80 and 0.05 <= height <= 1 and width >= 4 * height)
        ):
            return False
        color = self.graphicstate.ncolor
        if color not in (None, 0) and not (
            isinstance(color, (list, tuple))
            and (
                tuple(color) == (0, 0, 0, 1) or len(color) in (1, 3) and not any(color)
            )
        ):
            return False
        data = xobj.get_data()
        decode = list_value(xobj.get("Decode", [0, 1]))
        if not data or decode not in ([0, 1], [1, 0]):
            return False
        if bool(data[0] & 128) != (decode == [1, 0]):
            return False
        start = apply_matrix_pt(self.ctm, (0, 0.5))
        end = apply_matrix_pt(self.ctm, (1, 0.5))
        layout = getattr(self.device, "layout", {}).get(self.device.cur_item.pageid)
        if layout is None:
            return False
        h, w = layout.shape
        if any(
            layout[int(np.clip(y, 0, h - 1)), int(np.clip(x, 0, w - 1))] <= 1
            for x, y in (start, end)
        ):
            return False
        state = self.graphicstate.copy()
        state.linewidth = height
        self.device.paint_path(
            state, True, False, False, [("m", 0, 0.5), ("l", 1, 0.5)]
        )
        return True

    def process_page(self, page: PDFPage) -> None:
        # 重载设置 page 的 obj_patch
        # log.debug("Processing page: %r", page)
        # print(page.mediabox,page.cropbox)
        # (x0, y0, x1, y1) = page.mediabox
        x0, y0, x1, y1 = page.cropbox
        if page.rotate == 90:
            ctm = (0, -1, 1, 0, -y0, x1)
        elif page.rotate == 180:
            ctm = (-1, 0, 0, -1, x1, y1)
        elif page.rotate == 270:
            ctm = (0, 1, -1, 0, y1, -x0)
        else:
            ctm = (1, 0, 0, 1, -x0, -y0)
        self.device.begin_page(page, ctm)
        ops_base = self.render_contents(page.resources, page.contents, ctm=ctm)
        self.device.fontid = self.fontid
        self.device.fontmap = self.fontmap
        ops_result = self.device.end_page(page)
        deferred_ops = ops_result if isinstance(ops_result, dict) else {}
        ops_new = "" if isinstance(ops_result, dict) else ops_result
        # 上面渲染的时候会根据 cropbox 减掉页面偏移得到真实坐标，这里输出的时候需要用 cm 把页面偏移加回来
        self.obj_patch[page.page_xref] = (
            f"q {ops_base}Q 1 0 0 1 {x0} {y0} cm {ops_new}"  # ops_base 里可能有图，需要让 ops_new 里的文字覆盖在上面，使用 q/Q 重置位置矩阵
        )
        for page_xref, translated_ops in deferred_ops.items():
            if page_xref not in self.obj_patch:
                raise ValueError(
                    f"deferred translation refers to unknown page xref {page_xref}"
                )
            self.obj_patch[page_xref] += translated_ops
        for obj in page.contents:
            self.obj_patch[obj.objid] = ""

    def render_contents(
        self,
        resources: Dict[object, object],
        streams: Sequence[object],
        ctm: Matrix = MATRIX_IDENTITY,
        initial_state=None,
    ) -> None:
        # 重载返回指令流
        """Render the content streams.

        This method may be called recursively.
        """
        # log.debug(
        #     "render_contents: resources=%r, streams=%r, ctm=%r",
        #     resources,
        #     streams,
        #     ctm,
        # )
        self.init_resources(resources)
        self.init_state(ctm)
        if initial_state is not None:
            # A Form inherits graphics and text state but uses its own CTM.
            self.set_current_state((ctm, *initial_state[1:]))
        return self.execute(list_value(streams))

    def execute(self, streams: Sequence[object]) -> None:
        # 重载返回指令流
        ops = ""
        try:
            parser = PDFContentParser(streams)
        except PSEOF:
            # empty page
            return
        while True:
            try:
                _, obj = parser.nextobject()
            except PSEOF:
                break
            if isinstance(obj, PSKeyword):
                name = keyword_name(obj)
                method = "do_%s" % name.replace("*", "_a").replace('"', "_w").replace(
                    "'",
                    "_q",
                )
                if hasattr(self, method):
                    func = getattr(self, method)
                    nargs = func.__code__.co_argcount - 1
                    if nargs:
                        args = self.pop(nargs)
                        # log.debug("exec: %s %r", name, args)
                        if len(args) == nargs:
                            replacement = func(*args)
                            if name == "Do" and replacement == "n":
                                ops += "n "
                                continue
                            if not (
                                name[0] == "T"
                                or name in ['"', "'", "EI", "MP", "DP", "BMC", "BDC"]
                            ):  # 过滤 T 系列文字指令，因为 EI 的参数是 obj 所以也需要过滤（只在少数文档中画横线时使用），过滤 marked 系列指令
                                p = " ".join(_serialize_pdf_operand(x) for x in args)
                                ops += f"{p} {name} "
                    else:
                        # log.debug("exec: %s", name)
                        targs = func()
                        if targs is None:
                            targs = []
                        if not (name[0] == "T" or name in ["BI", "ID", "EMC"]):
                            p = " ".join(_serialize_pdf_operand(x) for x in targs)
                            if name == "S" and getattr(
                                self, "_pending_uri_underline_marker", ""
                            ):
                                ops += self._pending_uri_underline_marker + "\n"
                                self._pending_uri_underline_marker = ""
                            ops += f"{p} {name} "
                elif settings.STRICT:
                    error_msg = "Unknown operator: %r" % name
                    raise PDFInterpreterError(error_msg)
            else:
                self.push(obj)
        # print('REV DATA',ops)
        return ops
