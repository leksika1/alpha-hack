"""OCR the bank's real analytics PDFs with the backend's own pipeline and save chunks for the eval harness.

The organisers' PDFs are scans (no text layer), so this is the path production takes: render page -> tesseract
rus+eng -> backend chunk_pages / document_meta. Output: eval/out/real_analytics_chunks.json (not committed —
derived from the organisers' materials), used by `run_eval --analytics`.

    cd ml && uv run python -m eval.real_analytics --pdf-dir "../data/analytics"   # needs tesseract + rus
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from app.domain.chunking import chunk_pages, document_meta
from app.integrations.ocr import TesseractOcr
from app.integrations.pdf import page_texts, render_png

HERE = Path(__file__).resolve().parent
NS = uuid.UUID("8d6e4c1a-5b7f-4e2d-9c3a-1f0e2d3c4b5a")


async def ocr_pdf(path: Path, ocr: TesseractOcr, sem: asyncio.Semaphore) -> dict[str, Any]:
    data = path.read_bytes()
    pages = page_texts(data, max_pages=60, max_bytes=60_000_000)

    async def one(n: int, text: str) -> tuple[int, str, float | None, str]:
        if len(text.strip()) >= 200:
            return n, text, None, "text_layer"
        async with sem:
            png = await asyncio.to_thread(render_png, data, n)
            res = await ocr.recognize(png, n)
        return n, res.text, res.confidence, "ocr"

    done = await asyncio.gather(*(one(p.number, p.text) for p in pages))
    texts = [(n, t) for n, t, _, _ in sorted(done)]
    meta = document_meta(texts, path.name)
    conf = [c for _, _, c, _ in done if c is not None]
    doc_id = str(uuid.uuid5(NS, path.name))
    chunks = [
        {
            "chunk_id": str(uuid.uuid5(NS, f"{path.name}:{c.page}:{c.index}")),
            "document_id": doc_id,
            "doc_title": meta.title,
            "doc_date": meta.doc_date.isoformat() if meta.doc_date else None,
            "page": c.page,
            "section": c.section,
            "text": c.text,
            "extraction": "ocr" if conf else "text_layer",
            "ocr_confidence": round(sum(conf) / len(conf), 3) if conf else None,
            "score": 0.0,
        }
        for c in chunk_pages(texts)
    ]
    return {
        "file": path.name,
        "title": meta.title,
        "date": meta.doc_date.isoformat() if meta.doc_date else None,
        "topics": meta.topics,
        "pages": len(pages),
        "ocr_confidence": round(sum(conf) / len(conf), 3) if conf else None,
        "chunks": chunks,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--out", default=str(HERE / "out" / "real_analytics_chunks.json"))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    ocr = TesseractOcr()
    sem = asyncio.Semaphore(args.workers)
    t0 = time.time()
    docs = []
    for pdf in sorted(Path(args.pdf_dir).glob("*.pdf")):
        d = await ocr_pdf(pdf, ocr, sem)
        print(
            f"{d['file']}: {d['pages']} pages, {len(d['chunks'])} chunks, conf {d['ocr_confidence']}, "
            f"title «{d['title'][:60]}», date {d['date']}, topics {d['topics']} ({time.time() - t0:.0f}s)",
            flush=True,
        )
        docs.append(d)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
