# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["mlx-embeddings==0.1.0"]
#
# [tool.uv]
# exclude-newer = "2026-10-25T00:00:00Z"
# ///
"""Local embeddings on an Apple Silicon Mac, with MLX, behind an OpenAI-style /v1/embeddings endpoint.

The app runs in Docker, where MLX can't reach the Mac's GPU, so this runs on the Mac itself and the
containers call it at http://host.docker.internal:8090 (set LOCAL_EMBED_URL in .env):

    uv run scripts/mlx_embed_server.py                  # Qwen/Qwen3-Embedding-0.6B on 127.0.0.1:8090

The first run downloads the model from Hugging Face (about 1.2 GB for the 0.6B model).

It serves one model and returns full-size, L2-normalised vectors in request order. Callers add any
query instruction and shorten vectors themselves. Requests are handled one at a time.
"""

import argparse
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import mlx.core as mx
from mlx_embeddings.utils import load

log = logging.getLogger("mlx-embed")

MAX_TOKENS = 2048  # longest input embedded; a chunk is at most ~1,500 non-whitespace characters
BATCH = 16  # inputs per forward pass, after sorting by length so padding stays small


class Embedder:
    def __init__(self, name: str):
        self.name = name
        self.model, self.tokenizer = load(name)
        self.dim = len(self.embed(["warm-up"])[0][0])

    def embed(self, texts: list[str]) -> tuple[list[list[float]], int]:
        """Vectors for `texts`, in order, and the number of tokens embedded."""
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        vectors: list[list[float] | None] = [None] * len(texts)
        tokens = 0
        for start in range(0, len(order), BATCH):
            batch = order[start:start + BATCH]
            inputs = self.tokenizer.batch_encode_plus(
                [texts[i] for i in batch], return_tensors="mlx", padding=True, truncation=True,
                max_length=MAX_TOKENS)
            outputs = self.model(inputs["input_ids"], attention_mask=inputs["attention_mask"])
            embeds = outputs.text_embeds
            embeds = embeds / mx.linalg.norm(embeds, axis=-1, keepdims=True)
            mx.eval(embeds)
            tokens += int(inputs["attention_mask"].sum().item())
            for i, vector in zip(batch, embeds.tolist()):
                vectors[i] = vector
        return vectors, tokens  # type: ignore[return-value]


def make_handler(embedder: Embedder) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path in ("/health", "/v1/models"):
                self._send(200, {"model": embedder.name, "dim": embedder.dim})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/v1/embeddings":
                return self._send(404, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
                if not texts or not all(isinstance(t, str) for t in texts):
                    raise ValueError("input must be a string or a non-empty list of strings")
            except (ValueError, KeyError, TypeError) as exc:
                return self._send(400, {"error": str(exc)})
            started = time.perf_counter()
            vectors, tokens = embedder.embed(texts)
            log.info("embedded %d inputs, %d tokens, in %.0f ms", len(texts), tokens,
                     (time.perf_counter() - started) * 1000)
            self._send(200, {
                "object": "list", "model": embedder.name,
                "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
            })

        def log_message(self, format: str, *args: object) -> None:  # the embed log line is enough
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    log.info("loading %s ...", args.model)
    embedder = Embedder(args.model)
    log.info("serving %s (%d dimensions) on http://%s:%d/v1/embeddings", args.model, embedder.dim, args.host, args.port)
    HTTPServer((args.host, args.port), make_handler(embedder)).serve_forever()


if __name__ == "__main__":
    main()
