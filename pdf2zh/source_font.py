"""Recover source font text from authoritative embedded encoding evidence."""

from io import BytesIO

from fontTools.cffLib import CFFFontSet
from pdfminer.encodingdb import name2unicode
from pdfminer.pdftypes import dict_value, stream_value
from pdfminer.psparser import literal_name


def repair_stix_replacement_mapping(font, spec) -> None:
    """Expose one known STIX horizontal glyph hidden by a broken ToUnicode.

    Some publisher PDFs map ``uniE0D5`` in STIXNonUnicode-Regular to U+FFFD,
    even though their explicit Encoding still identifies the source glyph.
    STIX calls it a single long chemical bond (Unicode L2/09-208, p. 20).
    Use an em dash as its readable text surrogate, including when the publisher
    uses this glyph for prose punctuation. This is not a canonical Unicode
    equivalence. Protected chemical drawings still render the original CID,
    width and font program; no PDF font resource is rewritten.

    Only the replacement-character mapping is repaired. Valid Unicode values,
    other fonts, and every other private-use glyph remain authoritative.
    """
    if literal_name(spec.get("Subtype")) != "Type1":
        return
    name = literal_name(spec.get("BaseFont"))
    if name.split("+")[-1] != "STIXNonUnicode-Regular":
        return
    if "Encoding" not in spec or "ToUnicode" not in spec:
        return
    mapping = getattr(getattr(font, "unicode_map", None), "cid2unichr", None)
    encoding = getattr(font, "cid2unicode", {})
    if not isinstance(mapping, dict):
        return
    for cid, text in mapping.items():
        if text == "\ufffd" and encoding.get(cid) == "\ue0d5":
            mapping[cid] = "\u2014"


def repair_embedded_cff_encoding(font, spec) -> None:
    """Use the embedded CFF Encoding when no PDF Encoding overrides it.

    pdfminer handles an embedded Type1 FontFile's encoding, but not the
    equivalent Type1C FontFile3 encoding. In such PDFs it falls back to
    StandardEncoding, turning ordinary ff/ffi ligatures into unknown CIDs.
    The font's own glyph names are authoritative; never infer missing glyphs
    from surrounding words or replace an explicit ToUnicode/Encoding map.
    """
    if getattr(font, "_pdf2zh_cff_encoding_checked", False):
        return
    font._pdf2zh_cff_encoding_checked = True
    if "Encoding" in spec or "ToUnicode" in spec:
        return
    if literal_name(spec.get("Subtype")) != "Type1":
        return
    descriptor = dict_value(spec.get("FontDescriptor", {}))
    if "FontFile3" not in descriptor:
        return
    try:
        stream = stream_value(descriptor["FontFile3"])
        if literal_name(stream.get("Subtype")) != "Type1C":
            return
        cff = CFFFontSet()
        cff.decompile(BytesIO(stream.get_data()), None)
        if len(cff.fontNames) != 1:
            return
        encoding = cff[cff.fontNames[0]].Encoding
        if not isinstance(encoding, list):
            return
        recovered = {}
        for cid, name in enumerate(encoding):
            if name == ".notdef":
                continue
            try:
                recovered[cid] = name2unicode(name)
            except (KeyError, ValueError):
                continue
        if recovered:
            font.cid2unicode = {**font.cid2unicode, **recovered}
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, EOFError):
        # Malformed/unsupported font programs retain the original glyph path.
        return
