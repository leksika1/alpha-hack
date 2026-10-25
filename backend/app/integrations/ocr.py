"""OCR providers (ADR-005): `tesseract` (local CLI, rus+eng) and `fake` (tests). The VLM provider
(Qwen3-VL via the LLM Gateway) is a backlog item."""

import asyncio
import shutil
from collections import defaultdict
from contextlib import suppress

from app.domain.ports import OcrPage

TESSERACT_TIMEOUT_S = 180


class OcrUnavailableError(RuntimeError):
    pass


MIN_LINE_CONF = 0.65  # lines below this mean word confidence are chart/picture noise ("[== [== ©")


def tsv_to_text(tsv: str) -> tuple[str, float | None]:
    """Rebuild text from `tesseract ... tsv` output: lines by (block, par, line), paragraphs separated by
    blank lines. Lines with a low mean word confidence are dropped; the returned confidence (0..1) is the
    mean over the words that were kept."""
    lines: dict[tuple[int, int, int], list[tuple[str, float | None]]] = defaultdict(list)
    for row in tsv.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        word = parts[11].strip()
        if not word:
            continue
        try:
            conf: float | None = float(parts[10]) / 100
        except ValueError:
            conf = None
        lines[(int(parts[2]), int(parts[3]), int(parts[4]))].append(
            (word, conf if conf is not None and conf >= 0 else None)
        )
    out: list[str] = []
    kept: list[float] = []
    prev_par: tuple[int, int] | None = None
    for key in sorted(lines):
        words = lines[key]
        confs = [c for _, c in words if c is not None]
        if confs and sum(confs) / len(confs) < MIN_LINE_CONF:
            continue
        block, par, _ = key
        if prev_par is not None and (block, par) != prev_par:
            out.append("")
        out.append(" ".join(w for w, _ in words))
        kept.extend(confs)
        prev_par = (block, par)
    return "\n".join(out), (round(sum(kept) / len(kept), 3) if kept else None)


class TesseractOcr:
    name = "ocr_tesseract"

    def __init__(self, languages: str = "rus+eng", binary: str = "tesseract") -> None:
        self.languages = languages
        self.binary = shutil.which(binary) or binary

    @staticmethod
    def available(binary: str = "tesseract") -> bool:
        return shutil.which(binary) is not None

    async def recognize(self, image_png: bytes, page: int) -> OcrPage:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary, "stdin", "stdout", "-l", self.languages, "--psm", "3", "tsv",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
        except FileNotFoundError as exc:
            raise OcrUnavailableError(
                "tesseract binary not found (install tesseract-ocr tesseract-ocr-rus)"
            ) from exc
        try:
            out, err = await asyncio.wait_for(proc.communicate(image_png), timeout=TESSERACT_TIMEOUT_S)
        except BaseException:
            if proc.returncode is None:
                with suppress(ProcessLookupError):
                    proc.kill()
            await proc.communicate()
            raise
        if proc.returncode != 0:
            raise OcrUnavailableError(f"tesseract failed: {err.decode(errors='replace')[:300]}")
        text, conf = tsv_to_text(out.decode("utf-8", errors="replace"))
        return OcrPage(page=page, text=text, confidence=conf, method=self.name)


class FakeOcr:
    """Tests/e2e: scanned pages yield no text (documents with a text layer are unaffected)."""

    name = "ocr_fake"

    async def recognize(self, image_png: bytes, page: int) -> OcrPage:
        return OcrPage(page=page, text="", confidence=None, method=self.name)


def get_ocr(provider: str) -> TesseractOcr | FakeOcr:
    if provider == "fake":
        return FakeOcr()
    if provider == "tesseract":
        return TesseractOcr()
    raise ValueError(f"OCR provider {provider!r} is not available (vlm: see docs/ROADMAP.md)")
