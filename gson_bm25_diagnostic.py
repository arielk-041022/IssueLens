import sys, json, hashlib
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "src"))
import bm25_baseline as bm25

base = root / "experiments" / "gson-supplement-v1"

cases = {
    "GSON-2144": {
        "title": "JsonPrimitive#equals Method behaves incorrect when used with BigInteger",
        "gt": [
            "java-source-v2:com.google.gson.JsonPrimitive#getAsBigInteger()",
            "java-source-v2:com.google.gson.JsonPrimitive#equals(java.lang.Object)"
        ]
    },
    "GSON-2334": {
        "title": "Malformed Unicode escape sequence causes NumberFormatException instead of MalformedJsonException",
        "gt": [
            "java-source-v2:com.google.gson.stream.JsonReader#readEscapeCharacter()"
        ]
    }
}

for case_id, config in cases.items():
    folder = base / case_id
    index_path = folder / "parent.index-v2.json"
    metadata_path = Path(str(index_path) + ".meta.json")

    raw = index_path.read_bytes()
    index = json.loads(raw)
    meta = json.loads(metadata_path.read_bytes())

    assert meta["index_sha256"] == hashlib.sha256(raw).hexdigest()
    assert meta["method_id_scheme"] == "java-source-v2"

    records = []
    documents = []

    for file_entry in index:
        for cls in file_entry["classes"]:
            for method in cls["methods"]:
                features = {
                    "name": method["name"],
                    "class": cls["name"],
                    "signature": method["signature"],
                    "file": file_entry["file"],
                    "annotations": method["annotations"],
                    "strings": method["strings"],
                    "calls": method["calls"]
                }

                record = {
                    "method_id": method.get("method_id"),
                    "file": file_entry["file"],
                    "class": cls["name"],
                    "signature": method["signature"],
                    "identity_status": method["identity_status"]
                }
                records.append(record)
                documents.append(
                    bm25.tokenize(bm25.document(features))
                )

    assert len(records) == meta["methods_total"]

    query = bm25.tokenize(config["title"])
    scores, _ = bm25.score_tokens(documents, query)

    order = sorted(
        range(len(records)),
        key=lambda i: (-scores[i], i)
    )

    ranking = []
    for rank, i in enumerate(order, 1):
        ranking.append({
            "rank": rank,
            "score": scores[i],
            **records[i]
        })

    result = {
        "status": "OFFLINE_DIAGNOSTIC_NOT_STRICT_V2",
        "case_id": case_id,
        "issue_title": config["title"],
        "index_sha256": hashlib.sha256(raw).hexdigest(),
        "bm25_code_sha256": hashlib.sha256(
            (root / "src" / "bm25_baseline.py").read_bytes()
        ).hexdigest(),
        "method_count": len(ranking),
        "identity_unavailable": meta["identity_unavailable_methods"],
        "ranking": ranking
    }

    output = folder / "bm25-diagnostic-v1.json"
    if output.exists():
        raise FileExistsError(f"Refusing overwrite: {output}")

    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"\n===== {case_id} =====")
    print("Total methods:", len(ranking))
    print("Identity unavailable:", result["identity_unavailable"])

    print("\nGT ranks:")
    for gt in config["gt"]:
        matches = [r for r in ranking if r["method_id"] == gt]
        if len(matches) != 1:
            print("NOT_UNIQUELY_MATCHED:", gt, "count=", len(matches))
        else:
            print("Rank", matches[0]["rank"], gt)

    print("\nTop-5:")
    for r in ranking[:5]:
        print(r["rank"], r["score"], r["method_id"] or
              f"UNAVAILABLE:{r['file']}:{r['signature']}")

    print("\nSaved:", output)
