"""Repeatable synthetic technical evaluation; run through `willow run`.

The explicit fixtures below are the oracle, not Willow's descriptions and not
an LLM. This is a regression benchmark, not evidence of participant usability.
"""
import argparse
import hashlib
import json
import os
import platform
import statistics
import tempfile
import time

import willow as W


def key(category, action, path, old_path=None, field=None, actor="unknown"):
    return (category, action, path, old_path, field, actor)


CASES = [
    ("appeared", {}, {"a.py": "a"}, [key("object", "appeared", "a.py")]),
    ("removed", {"a.py": "a"}, {}, [key("object", "removed", "a.py")]),
    ("modified", {"a.py": "a"}, {"a.py": "b"}, [key("attribute", "modified", "a.py")]),
    ("move", {"a.py": "a"}, {"src/a.py": "a"}, [key("spatial", "relocated", "src/a.py", "a.py")]),
    ("unchanged", {"a.py": "a"}, {"a.py": "a"}, []),
    ("duplicate ambiguity", {"a.py": "a", "b.py": "a"}, {"c.py": "a"},
     [key("object", "removed", "a.py"), key("object", "removed", "b.py"), key("object", "appeared", "c.py")]),
    ("settings", {"settings.json": '{"theme":"light"}'}, {"settings.json": '{"theme":"dark"}'},
     [key("attribute", "modified", "settings.json"), key("attribute", "modified", "settings.json", field="theme")]),
    ("package addition", {"package.json": '{"dependencies":{}}'}, {"package.json": '{"dependencies":{"example":"1"}}'},
     [key("attribute", "modified", "package.json"), key("object", "appeared", "package.json", field="dependencies.example")]),
    ("package removal", {"package.json": '{"dependencies":{"example":"1"}}'}, {"package.json": '{"dependencies":{}}'},
     [key("attribute", "modified", "package.json"), key("object", "removed", "package.json", field="dependencies.example")]),
    ("package version", {"package.json": '{"dependencies":{"example":"1"}}'}, {"package.json": '{"dependencies":{"example":"2"}}'},
     [key("attribute", "modified", "package.json"), key("attribute", "modified", "package.json", field="dependencies.example")]),
    ("unreadable means unknown", {"a.py": "a"}, {}, []),
    ("matching agent hook", {"a.py": "a"}, {"a.py": "b"}, [key("attribute", "modified", "a.py", actor="agent")]),
    ("stale agent hook", {"a.py": "a"}, {"a.py": "b"}, [key("attribute", "modified", "a.py")]),
    ("declared human", {"a.py": "a"}, {"a.py": "b"}, [key("attribute", "modified", "a.py", actor="user")]),
    ("binary modified", {"a.bin": b"\x00\xffa"}, {"a.bin": b"\x00\xffb"}, [key("attribute", "modified", "a.bin")]),
    ("unicode path", {}, {"hello world/\u30c6\u30b9\u30c8.py": "x"}, [key("object", "appeared", "hello world/\u30c6\u30b9\u30c8.py")]),
]


def write_files(folder, files):
    for path, data in files.items():
        full = os.path.join(folder, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def scan(folder):
    eyes = W.Eyes(folder)
    eyes.prime()
    return W.memory_snapshot(eyes)


def evaluate(revisits=110):
    tp = fp = fn = repetitions = 0
    results, latencies = [], []
    for name, before_files, after_files, oracle in CASES:
        with tempfile.TemporaryDirectory(prefix="willow-eval-") as folder:
            write_files(folder, before_files)
            before = scan(folder)
            for path in before_files:
                os.remove(os.path.join(folder, path))
            write_files(folder, after_files)
            after = scan(folder)
            provenance = []
            if name == "unreadable means unknown":
                after["uncertain"] = ["a.py"]
            if name in ("matching agent hook", "stale agent hook", "declared human"):
                digest = hashlib.sha1(b"stale" if name == "stale agent hook" else b"b").hexdigest()
                provenance = [{"path": "a.py", "after_hash": digest,
                               "actor": "user" if name == "declared human" else "agent",
                               "source": "synthetic fixture"}]
            started = time.perf_counter()
            predicted = W.compare_snapshots(before, after, provenance)
            elapsed = (time.perf_counter() - started) * 1000
            latencies.append(elapsed)
            tuples = [key(c["category"], c["action"], c["path"], c.get("old_path"), c.get("field"), c["actor"])
                      for c in predicted]
            actual, expected = set(tuples), set(oracle)
            tp += len(actual & expected)
            fp += len(actual - expected)
            fn += len(expected - actual)
            repetitions += len(tuples) - len(actual)
            results.append({"case": name, "passed": actual == expected,
                            "expected": sorted(oracle, key=str), "predicted": sorted(tuples, key=str),
                            "comparison_ms": elapsed})
    storage, visit_latencies = [], []
    with tempfile.TemporaryDirectory(prefix="willow-scale-") as folder:
        memory = W.ProjectMemory(folder, W.DEFAULTS)
        before = scan(folder)
        for i in range(revisits):
            started = time.perf_counter()
            write_files(folder, {"counter.py": str(i)})
            current = scan(folder)
            changes = W.compare_snapshots(before, current)
            memory.begin(current, {}, changes)
            memory.checkpoint(current, {}, closed=True)
            before = current
            visit_latencies.append((time.perf_counter() - started) * 1000)
            storage.append(os.path.getsize(memory.path))
        with memory.db() as db:
            sessions = db.execute("SELECT count(*) FROM sessions").fetchone()[0]
            facts = db.execute("SELECT count(*) FROM facts").fetchone()[0]
    precision = tp / (tp + fp) if tp + fp else 1
    recall = tp / (tp + fn) if tp + fn else 1
    with open(W.__file__, "rb") as f:
        code_hash = hashlib.sha256(f.read()).hexdigest()
    return {"evaluation": "synthetic fixtures; no participant data", "willow_version": W.__version__,
            "willow_sha256": code_hash, "platform": platform.platform(), "python": platform.python_version(),
            "fixture_count": len(CASES), "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
            "repeated_facts": repetitions,
            "comparison_ms_mean": statistics.mean(latencies), "cases": results,
            "scaling": {"revisits": revisits, "retained_sessions": sessions, "retained_facts": facts,
                        "database_bytes": storage[-1], "peak_database_bytes": max(storage),
                        "checkpoint_ms_mean": statistics.mean(visit_latencies),
                        "checkpoint_ms_max": max(visit_latencies)},
            "limits": ["Synthetic small projects, not representative user workspaces.",
                       "No screen-reader usability or speech collision measurement.",
                       "Hardware measurements apply only to the reported host platform.",
                       "Known unsupported cases and rename inference need manual review."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".willow/evaluation.json")
    parser.add_argument("--revisits", type=int, default=110)
    args = parser.parse_args()
    if args.revisits < 1:
        parser.error("--revisits must be positive")
    result = evaluate(args.revisits)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    passed = sum(c["passed"] for c in result["cases"])
    print(f"Synthetic fixtures: {passed}/{result['fixture_count']}; "
          f"precision {result['precision']:.3f}, recall {result['recall']:.3f}, F1 {result['f1']:.3f}.")
    print(f"Scaling: {args.revisits} revisits, {result['scaling']['retained_sessions']} retained sessions, "
          f"{result['scaling']['database_bytes']} database bytes.")
    print("Report: " + os.path.abspath(args.output))
    return 0 if passed == result["fixture_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
