"""Project recorded JSON into a compact README preview; never invent rows."""
import json
import sys
r = json.load(sys.stdin)
print(r["status"], "·", len(r["candidates"]), "URLs")
print(" + ".join(item["source"] for item in r["candidates"][:2]))
print("rerank:", r["rerank"])
print("failed:", " + ".join(item["channel"] for item in r["failed_channels"]))
