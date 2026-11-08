"""Select the deployment checkpoint using matching validation partitions only."""
import json
import shutil
from pathlib import Path

CANDIDATES = (Path("runs/rollerball_targets"), Path("runs/rollerball_slow"))
OUTPUT = Path("runs/rollerball_active")


def main():
    reviewed = json.loads(Path("data/rollerball_pens/split.json").read_text())
    records = []
    for root in CANDIDATES:
        split = json.loads((root/"split.json").read_text())
        if split["classes"] != reviewed["classes"] or split["splits"] != reviewed["splits"]:
            raise ValueError(f"Candidate has different partitions: {root}")
        metrics = json.loads((root/"metrics.json").read_text())
        validation = metrics[metrics["selected_model"]]["val"]
        objective = validation["balanced_accuracy"]-.5*validation["pen_false_accept_rate"]
        key = (objective, -validation["pen_false_accept_rate"], -validation["negative_false_accept_rate"])
        records.append(dict(root=str(root), key=key, validation=validation))
    selected = max(records, key=lambda record: record["key"])
    root = Path(selected["root"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("best.pt", "metrics.json"):
        shutil.copyfile(root/name, OUTPUT/name)
    (OUTPUT/"split.json").write_text(json.dumps(reviewed, indent=2))
    (OUTPUT/"selection.json").write_text(json.dumps(dict(selected=str(root),
        candidates=records, policy="Validation balanced accuracy minus half the pen false-accept rate; test results excluded from selection"), indent=2))
    print("Selected", root, "using validation results only.")


if __name__ == "__main__":
    main()
