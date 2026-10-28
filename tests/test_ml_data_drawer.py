"""Guard tests for the data-drawer durable artifact store (Free1).

Invariants under test:
  - layout: <root>/<identifier>/sub-train-<PAIR>_<TS>/ with caller
    artifacts + run_metadata.json; index at pair_dictionary.json
  - atomic latest-wins: a fresh save rewrites the index; the previous run
    dir remains as history; same-timestamp re-save conflicts
  - content-addressed reads: a bit-rotted artifact fails latest() with
    DrawerCorrupt, never loads
  - fail-closed: unknown identifiers/pairs raise DrawerEmpty (no implicit
    creation); corrupt index / missing artifact / torn disagree raise
    DrawerCorrupt; bad names, non-bytes artifacts, reserved names,
    non-int/bad timestamps raise typed input errors
  - retention: purge keeps the newest (index pointer) and removes the
    oldest beyond keep; never deletes the newest; keep<1 refused;
    part-pair foreign dirs untouched
Fully offline, hermetic — everything under tmp_path.
"""
import json
from pathlib import Path

import pytest

from ml.data_drawer import (
    INDEX_FILE,
    RUN_RE,
    DataDrawer,
    DrawerConflict,
    DrawerCorrupt,
    DrawerEmpty,
)


def _artifacts() -> dict:
    return {
        "model.joblib": b"pickled-model-bytes-v1",
        "feature_pipeline.pkl": b"pipeline-bytes-v1",
    }


def _meta(extra=None) -> dict:
    base = {"feature_cols": ["f0", "f1"], "trained_at": "2026-10-28T00:00:00Z"}
    if extra:
        base.update(extra)
    return base


def _save(drawer: DataDrawer, **overrides):
    kwargs = dict(
        identifier="carry-quantile",
        pair="SOL-USDT",
        trained_timestamp=1_700_000_000_000,
        artifacts=_artifacts(),
        metadata=_meta(),
    )
    kwargs.update(overrides)
    return drawer.save(**kwargs)


def test_layout_and_index_after_first_save(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    entry = _save(drawer)
    run_dir = tmp_path / "carry-quantile" / f"sub-train-SOL-USDT_{1_700_000_000_000:013d}"
    assert run_dir.is_dir()
    assert (run_dir / "model.joblib").read_bytes() == b"pickled-model-bytes-v1"
    assert (run_dir / "run_metadata.json").is_file()
    index = json.loads(
        (tmp_path / "carry-quantile" / INDEX_FILE).read_text(encoding="utf-8")
    )
    assert index["SOL-USDT"] == entry
    assert entry["digests"]["model.joblib"] == __import__("hashlib").sha256(
        b"pickled-model-bytes-v1").hexdigest()


def test_latest_roundtrip(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer, trained_timestamp=1_700_000_000_000, metadata=_meta({"n": 1}))
    entry = drawer.latest(identifier="carry-quantile", pair="SOL-USDT")
    assert entry["trained_timestamp"] == 1_700_000_000_000
    assert entry["metadata"]["n"] == 1


def test_save_then_newer_save_is_latest_wins_with_history_preserved(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    first = _save(drawer, trained_timestamp=1_700_000_000_000, metadata=_meta({"gen": 1}))
    second = _save(drawer, trained_timestamp=1_700_000_000_500, metadata=_meta({"gen": 2}))
    assert first["trained_timestamp"] != second["trained_timestamp"]
    entry = drawer.latest(identifier="carry-quantile", pair="SOL-USDT")
    assert entry["trained_timestamp"] == 1_700_000_000_500
    assert entry["metadata"]["gen"] == 2
    # old run dir NOT purged implicitly
    runs = drawer.list_runs(identifier="carry-quantile", pair="SOL-USDT")
    assert [r["trained_timestamp"] for r in runs] == [
        1_700_000_000_500, 1_700_000_000_000]


def test_same_timestamp_resave_conflicts(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer)
    with pytest.raises(DrawerConflict):
        _save(drawer)


def test_bit_rot_detected_on_latest(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer)
    run_dir = next((tmp_path / "carry-quantile").glob("sub-train-SOL-USDT_*"))
    model = run_dir / "model.joblib"
    model.write_bytes(b"pickled-model-bytes-BITROT")
    with pytest.raises(DrawerCorrupt):
        drawer.latest(identifier="carry-quantile", pair="SOL-USDT")
    # and the healthy artifact still matches — corruption detection is precise
    (run_dir / "model.joblib").write_bytes(b"pickled-model-bytes-v1")
    assert drawer.latest(identifier="carry-quantile", pair="SOL-USDT")


def test_unknown_pair_and_identifier_fail_closed(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    with pytest.raises(DrawerEmpty):
        drawer.latest(identifier="carry-quantile", pair="NOPE-USDT")
    # identifier without any drawer at all
    with pytest.raises(DrawerEmpty):
        drawer.latest(identifier="never-saved", pair="SOL-USDT")


def test_corrupt_index_detected(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer)
    index_path = tmp_path / "carry-quantile" / INDEX_FILE
    index_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DrawerCorrupt):
        drawer.latest(identifier="carry-quantile", pair="SOL-USDT")
    index_path.write_text('["not", "a", "dict"]', encoding="utf-8")
    with pytest.raises(DrawerCorrupt):
        drawer.list_runs(identifier="carry-quantile", pair="SOL-USDT")


def test_missing_artifact_detected(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer)
    run_dir = next((tmp_path / "carry-quantile").glob("sub-train-SOL-USDT_*"))
    (run_dir / "model.joblib").unlink()
    with pytest.raises(DrawerCorrupt):
        drawer.latest(identifier="carry-quantile", pair="SOL-USDT")


def test_run_metadata_disagreement_is_torn_write_detected(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer)
    run_dir = next((tmp_path / "carry-quantile").glob("sub-train-SOL-USDT_*"))
    meta_path = run_dir / "run_metadata.json"
    disk = json.loads(meta_path.read_text(encoding="utf-8"))
    disk["digests"]["model.joblib"] = "0" * 64
    meta_path.write_text(json.dumps(disk), encoding="utf-8")
    with pytest.raises(DrawerCorrupt):
        drawer.latest(identifier="carry-quantile", pair="SOL-USDT")


def test_index_points_at_missing_run_dir(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer)
    run_dir = next((tmp_path / "carry-quantile").glob("sub-train-SOL-USDT_*"))
    import shutil as _sh
    _sh.rmtree(run_dir)
    with pytest.raises(DrawerCorrupt):
        drawer.latest(identifier="carry-quantile", pair="SOL-USDT")
    with pytest.raises(DrawerCorrupt):
        drawer.purge(identifier="carry-quantile", pair="SOL-USDT", keep=1)


@pytest.mark.parametrize("kwargs", [
    dict(identifier="bad name!"),
    dict(pair="SOL/USDT:SWAP"),
    dict(trained_timestamp=1_700_000_000_000.5),
    dict(trained_timestamp=True),
    dict(trained_timestamp=-1),
    dict(artifacts={}),
    dict(artifacts={"model.joblib": "not-bytes"}),
    dict(artifacts={"b@d name": b"x"}),
    dict(artifacts={"metadata": b"reserved"}),
])
def test_save_fail_closed(tmp_path: Path, kwargs):
    drawer = DataDrawer(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        _save(drawer, **kwargs)
    # nothing was created
    assert not (tmp_path / "carry-quantile").exists() if kwargs.get("artifacts") else (
        not any(RUN_RE.match(p.name) for p in (tmp_path / "carry-quantile").glob("*"))
    )


def test_purge_keeps_newest_oldest_beyond_keep(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer, trained_timestamp=1_700_000_000_000)
    _save(drawer, trained_timestamp=1_700_000_000_500)
    _save(drawer, trained_timestamp=1_700_000_001_000)
    removed = drawer.purge(identifier="carry-quantile", pair="SOL-USDT", keep=1)
    assert removed == [f"sub-train-SOL-USDT_{1_700_000_000_500:013d}",
                       f"sub-train-SOL-USDT_{1_700_000_000_000:013d}"]
    left = sorted(p.name for p in (tmp_path / "carry-quantile").glob("sub-train-*"))
    assert left == [f"sub-train-SOL-USDT_{1_700_000_001_000:013d}"]
    assert drawer.latest(identifier="carry-quantile", pair="SOL-USDT")["trained_timestamp"] == 1_700_000_001_000
    # keep=2 on a single run: nothing removed
    assert drawer.purge(identifier="carry-quantile", pair="SOL-USDT", keep=2) == []


def test_purge_refuses_keep_below_one_and_unknown(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer)
    with pytest.raises(ValueError):
        drawer.purge(identifier="carry-quantile", pair="SOL-USDT", keep=0)
    with pytest.raises(ValueError):
        drawer.purge(identifier="carry-quantile", pair="SOL-USDT", keep=-3)
    with pytest.raises(DrawerEmpty):
        drawer.purge(identifier="carry-quantile", pair="NOPE-USDT", keep=1)


def test_purge_is_pair_scoped(tmp_path: Path):
    drawer = DataDrawer(tmp_path)
    _save(drawer, pair="SOL-USDT", trained_timestamp=1_700_000_000_000)
    _save(drawer, pair="BTC-USDT", trained_timestamp=1_700_000_000_000)
    drawer.purge(identifier="carry-quantile", pair="SOL-USDT", keep=1)
    # BTC is untouched
    assert drawer.latest(identifier="carry-quantile", pair="BTC-USDT") is not None


def test_run_filename_regex_roundtrip():
    m = RUN_RE.match("sub-train-SOL-USDT_1700000000000")
    assert m.group("pair") == "SOL-USDT" and m.group("ts") == "1700000000000"
    assert RUN_RE.match("sub-train-SOL-USDT_170000000000") is None  # 12 digits
    assert RUN_RE.match("sub-train-SOL USDT_1700000000000") is None


def test_flat_sidecar_data_is_untouched(tmp_path: Path):
    """The drawer must not migrate/lock/delete the existing flat sidecar
    tree (`models/carry_quantile/meta.json`) — additive by contract."""
    legacy = tmp_path / "carry_quantile"
    legacy.mkdir()
    (legacy / "meta.json").write_text('{"schema": "test"}', encoding="utf-8")
    drawer = DataDrawer(tmp_path)
    _save(drawer)
    assert json.loads((legacy / "meta.json").read_text(encoding="utf-8")) == {
        "schema": "test"}
