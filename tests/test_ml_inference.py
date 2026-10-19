"""TODO 4.5: feature-drift hard-fail on model reuse.

The live signal path passed 6 features against a 26-column model while
prepare_ silently zero-filled the other 20 — a stale model producing
garbage with a straight face. Now: the persisted schema is validated at
load, missing keys raise (never zero-fill), raw arrays are width-checked,
and absent artifacts raise instead of predicting zeros.

Hermetic by design: models/ is gitignored, so no test may require the
real artifacts (artifact-dependent checks skip when absent).
"""
import ast

import numpy as np
import pytest

import ml.carry_inference as ci


@pytest.fixture()
def schema3(monkeypatch):
    """Three-column schema + loaded flag, no filesystem involved."""
    monkeypatch.setattr(ci, "_feature_cols", ["a", "b", "c"])
    monkeypatch.setattr(ci, "_loaded", True)
    return ["a", "b", "c"]


def _row(**kw):
    base = {"a": 1.0, "b": 2.0, "c": 3.0}
    base.update(kw)
    return base


def test_full_row_ok_in_schema_order(schema3):
    arr = ci.prepare_features_for_inference({"c": 30.0, "a": 10.0, "b": 20.0})
    assert arr.shape == (1, 3)
    assert list(arr[0]) == [10.0, 20.0, 30.0]


def test_partial_row_raises_naming_missing(schema3):
    """The live 6-of-26 shape: missing keys raise, naming every one."""
    with pytest.raises(ci.FeatureDriftError) as exc:
        ci.prepare_features_for_inference({"a": 1.0})
    assert "b" in str(exc.value) and "c" in str(exc.value)


def test_none_value_counts_as_missing(schema3):
    with pytest.raises(ci.FeatureDriftError):
        ci.prepare_features_for_inference(_row(b=None))


def test_non_dict_row_raises(schema3):
    with pytest.raises(ci.FeatureDriftError):
        ci.prepare_features_for_inference([1.0, 2.0, 3.0])


def test_bool_value_rejected_as_coercion(schema3):
    with pytest.raises(ci.FeatureDriftError):
        ci.prepare_features_for_inference(_row(b=True))


def test_non_numeric_value_rejected(schema3):
    with pytest.raises(ci.FeatureDriftError):
        ci.prepare_features_for_inference(_row(b="high"))


def test_nan_passes_through_not_zeroed(schema3):
    """NaN is native to LightGBM; zero-filling it faked completeness."""
    arr = ci.prepare_features_for_inference(_row(b=float("nan")))
    assert np.isnan(arr[0][1])
    assert arr[0][0] == 1.0 and arr[0][2] == 3.0


@pytest.mark.parametrize("bad", [
    [], {}, "abc", None, [""], ["a", "a"], [True], [1], ["a", 2],
])
def test_schema_validation_rejects_drift(bad):
    with pytest.raises(ci.FeatureDriftError):
        ci.validate_feature_schema(bad)


def test_schema_validation_accepts_good_list():
    assert ci.validate_feature_schema(["x", "y"]) == ["x", "y"]


def test_width_mismatch_raises(monkeypatch):
    """Raw-array callers bypass prepare_: the width gate lives at predict."""
    class Stub:
        def predict(self, X):
            raise AssertionError("must not reach the model on drift")

    monkeypatch.setattr(ci, "_feature_cols", ["a", "b", "c"])
    monkeypatch.setattr(ci, "_loaded", True)
    monkeypatch.setattr(ci, "_q20_model", Stub())
    with pytest.raises(ci.FeatureDriftError):
        ci.predict_q20(np.zeros((1, 2)))


def test_missing_model_raises_not_zeros(monkeypatch):
    """No model must never produce a prediction-shaped zero (a default
    inside the value domain that reads as a real q20=0)."""
    monkeypatch.setattr(ci, "_feature_cols", ["a", "b", "c"])
    monkeypatch.setattr(ci, "_loaded", True)
    monkeypatch.setattr(ci, "_q20_model", None)
    with pytest.raises(ci.FeatureDriftError):
        ci.predict_q20(np.zeros((1, 3)))


def test_absent_artifacts_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(ci, "MODELS_DIR", tmp_path / "empty")
    monkeypatch.setattr(ci, "_loaded", False)
    monkeypatch.setattr(ci, "_q20_model", None)
    monkeypatch.setattr(ci, "_q50_model", None)
    monkeypatch.setattr(ci, "_feature_cols", None)
    with pytest.raises(ci.FeatureDriftError):
        ci._load_models()


def test_drift_error_is_runtime_error_for_degradation_path():
    """S5 catches Exception -> typed NEUTRAL; the drift error must flow
    through that exact path, and be importable where callers live."""
    assert issubclass(ci.FeatureDriftError, RuntimeError)
    from src.ml_inference import FeatureDriftError as Reexported
    assert Reexported is ci.FeatureDriftError


def test_train_schema_matches_persisted_artifact():
    """The training declaration and the artifact must agree (parsed via AST:
    no heavy imports). Skips where artifacts are absent (models/ is local)."""
    import joblib
    from pathlib import Path
    artifact = Path("models/carry_quantile/features.pkl")
    if not artifact.exists():
        pytest.skip("no local model artifacts (models/ is gitignored)")
    persisted = joblib.load(artifact)
    tree = ast.parse(Path("ml/train_carry.py").read_text())
    declared = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FEATURE_COLS":
            declared = ast.literal_eval(node.value)
    assert declared is not None
    assert list(persisted) == list(declared)


def test_live_row_shape_against_real_artifact():
    """End-to-end honesty where artifacts exist: the exact 6-key dict the
    live signal path builds must NOT satisfy the persisted schema."""
    import joblib
    from pathlib import Path
    artifact = Path("models/carry_quantile/features.pkl")
    if not artifact.exists():
        pytest.skip("no local model artifacts (models/ is gitignored)")
    cols = ci.validate_feature_schema(joblib.load(artifact))
    assert len(cols) > 6  # the model genuinely wants more than the live row
    live_row = {"f0": 0.001, "f1": 5.0, "f2": 0.01,
                "f_del": 0.001, "f_mean3d": 0.001, "f_z30d": 0.5}
    missing = [c for c in cols if c not in live_row]
    assert len(missing) == len(cols) - 6  # documents today's gap precisely


class TestDoPredictShield:
    """TODO 4.6: per-row novelty gate. 1 ok, 0 shield, 2 expired model.
    Strategy gates entry on flag == 1."""

    @pytest.fixture()
    def schema3(self, monkeypatch):
        monkeypatch.setattr(ci, "_feature_cols", ["a", "b", "c"])
        monkeypatch.setattr(ci, "_loaded", True)
        return ["a", "b", "c"]

    def test_clean_rows_pass(self, schema3):
        flags = ci.do_predict(np.array([[1.0, 2.0, 3.0], [0.0, -1.5, 2.5]]))
        assert list(flags) == [1, 1]
        assert flags.dtype == int

    def test_nan_row_shields_not_predicts(self, schema3):
        flags = ci.do_predict(np.array([[1.0, float("nan"), 3.0]]))
        assert list(flags) == [0]

    def test_inf_row_shields(self, schema3):
        flags = ci.do_predict(np.array([[1.0, float("inf"), 3.0]]))
        assert list(flags) == [0]

    def test_batch_mixed_flags_per_row(self, schema3):
        flags = ci.do_predict(np.array([
            [1.0, 2.0, 3.0],
            [1.0, float("nan"), 3.0],
            [0.0, 0.0, 0.0],
        ]))
        assert list(flags) == [1, 0, 1]

    def test_out_of_bounds_shields_when_bounds_given(self, schema3):
        lo = np.array([0.0, 0.0, 0.0])
        hi = np.array([10.0, 10.0, 10.0])
        assert list(ci.do_predict(np.array([[5.0, 5.0, 5.0]]), (lo, hi))) == [1]
        assert list(ci.do_predict(np.array([[50.0, 5.0, 5.0]]), (lo, hi))) == [0]

    def test_no_bounds_means_finiteness_only(self, schema3):
        # Documented, not faked: without training distribution stats (4.9
        # sidecar), a finite in-range-unknown row passes.
        assert list(ci.do_predict(np.array([[1e9, 2.0, 3.0]]))) == [1]

    def test_expired_model_flags_all_rows(self, schema3):
        flags = ci.do_predict(
            np.array([[1.0, 2.0, 3.0]]),
            model_trained_at=1_000_000.0, max_model_age_s=3600.0,
        )
        assert list(flags) == [2]

    def test_fresh_model_not_expired(self, schema3):
        import time
        flags = ci.do_predict(
            np.array([[1.0, 2.0, 3.0]]),
            model_trained_at=time.time(), max_model_age_s=3600.0,
        )
        assert list(flags) == [1]

    def test_no_timestamps_never_expired(self, schema3):
        # Age unknown is not "fresh" and not "expired": finiteness decides.
        assert list(ci.do_predict(np.array([[1.0, 2.0, 3.0]]))) == [1]

    def test_width_mismatch_raises_not_shields(self, schema3):
        # Shape drift is a caller bug (FeatureDriftError), never a shielded row.
        with pytest.raises(ci.FeatureDriftError):
            ci.do_predict(np.zeros((1, 2)))

    def test_shielded_row_never_reaches_model(self, monkeypatch):
        """A NaN row produces shield metadata, not a prediction: the model
        stub raises if touched."""
        class NoTouch:
            def predict(self, X):
                raise AssertionError("shielded row reached the model")

        monkeypatch.setattr(ci, "_feature_cols", ["a", "b", "c"])
        monkeypatch.setattr(ci, "_loaded", True)
        monkeypatch.setattr(ci, "_q20_model", NoTouch())
        monkeypatch.setattr(ci, "_q50_model", NoTouch())
        out = ci.predict_carry_decision({"a": 1.0, "b": float("nan"), "c": 3.0})
        assert out["shielded"] is True
        assert out["novelty_flag"] == 0
        assert out["eligible"] is False

    def test_clean_row_reaches_model(self, monkeypatch):
        class Stub:
            def __init__(self, v):
                self.v = v
            def predict(self, X):
                assert X.shape == (1, 3)
                return np.array([self.v])

        monkeypatch.setattr(ci, "_feature_cols", ["a", "b", "c"])
        monkeypatch.setattr(ci, "_loaded", True)
        monkeypatch.setattr(ci, "_q20_model", Stub(20.0))
        monkeypatch.setattr(ci, "_q50_model", Stub(25.0))
        out = ci.predict_carry_decision({"a": 1.0, "b": 2.0, "c": 3.0})
        assert out["shielded"] is False
        assert out["novelty_flag"] == 1
        assert out["eligible"] is True
        assert out["q20_bps"] == 20.0


def _passing_market():
    return dict(
        asset="BTC-USDT-SWAP",
        spot_price=100.0,
        perp_price=100.1,
        funding_rate=0.005,
        funding_history=[0.004, 0.005, 0.006],
        price_history=[99.0, 99.5, 100.0, 100.1],
    )


def test_strategy_gates_entry_on_shield(monkeypatch):
    """A shielded model verdict degrades (S5) instead of becoming a silent NO."""
    import src.ml_inference as ml_inference
    from src.signals import ML_DEGRADATIONS, ml_funding_carry_signal
    ML_DEGRADATIONS.clear()
    monkeypatch.setattr(
        ml_inference, "predict_carry_decision",
        lambda *a, **k: {"eligible": False, "q20_bps": 0.0, "q50_bps": 0.0,
                         "pin_state": 0, "survival_prob": 1.0,
                         "expected_pnl": 0.0,
                         "novelty_flag": 0, "shielded": True},
    )
    sig = ml_funding_carry_signal(**_passing_market())
    assert sig.direction == "NEUTRAL"
    assert sig.metadata["degraded"] is True
    assert sig.metadata["degradation_reason"].startswith("ml_shielded:")
    assert any("ml_shielded" in k for k in ML_DEGRADATIONS)
    ML_DEGRADATIONS.clear()


def test_strategy_ignores_absent_shield_keys(monkeypatch):
    """Backward compat: pre-shield verdict dicts (no shield keys) behave
    exactly as before — no phantom degradation."""
    import src.ml_inference as ml_inference
    from src.signals import ml_funding_carry_signal
    monkeypatch.setattr(
        ml_inference, "predict_carry_decision",
        lambda *a, **k: {"eligible": False, "q20_bps": 5.0},
    )
    sig = ml_funding_carry_signal(**_passing_market())
    assert sig.direction == "NEUTRAL"
    assert sig.metadata["degraded"] is False
    assert sig.metadata["degradation_reason"] is None


class _StubModel:
    """Picklable stand-in for a LightGBM quantile model (module level so
    joblib round-trips it in-process)."""

    def __init__(self, v=20.0):
        self.v = v

    def predict(self, X):
        return np.array([self.v] * len(X))


@pytest.fixture()
def model_dir(tmp_path, monkeypatch):
    """Isolated artifact dir: stub q20/q50 + 3-col schema. No meta.json
    unless a test writes one."""
    import joblib
    d = tmp_path / "models"
    d.mkdir()
    joblib.dump(["a", "b", "c"], d / "features.pkl")
    joblib.dump(_StubModel(20.0), d / "q20.pkl")
    joblib.dump(_StubModel(25.0), d / "q50.pkl")
    monkeypatch.setattr(ci, "MODELS_DIR", d)
    monkeypatch.setattr(ci, "_loaded", False)
    monkeypatch.setattr(ci, "_q20_model", None)
    monkeypatch.setattr(ci, "_q50_model", None)
    monkeypatch.setattr(ci, "_feature_cols", None)
    monkeypatch.setattr(ci, "_model_meta", {})
    return d


def _write_meta(d, **overrides):
    import json
    import time
    meta = {
        "meta_version": 1,
        "feature_cols": ["a", "b", "c"],
        "trained_at": time.time(),
        "n_rows": 100,
        "symbols": ["BTC"],
        "dataset_sha256": "abc",
        "quantiles": [0.2, 0.5],
        "code_version": "test",
    }
    meta.update(overrides)
    (d / "meta.json").write_text(json.dumps(meta))
    return meta


class TestModelMetadataSidecar:
    """TODO 4.8: training sidecar persisted at train time, refused at load
    on mismatch, feeding the 4.6 expiry shield. Absent sidecar (all
    pre-sidecar artifacts) never bricks a load."""

    def test_meta_loaded_and_exposed(self, model_dir):
        import time
        now = time.time()
        _write_meta(model_dir, trained_at=now)
        ci._load_models()
        assert ci.get_model_metadata()["trained_at"] == now

    def test_meta_schema_mismatch_raises(self, model_dir):
        _write_meta(model_dir, feature_cols=["a", "b"])
        with pytest.raises(ci.FeatureDriftError):
            ci._load_models()

    def test_absent_meta_loads_unassessed(self, model_dir):
        ci._load_models()  # no meta.json: loads fine, age unknown
        assert ci.get_model_metadata() == {}

    def test_malformed_meta_ignored(self, model_dir):
        (model_dir / "meta.json").write_text("{oops")
        ci._load_models()
        assert ci.get_model_metadata() == {}

    def test_expired_model_shields_decision(self, model_dir):
        import time
        _write_meta(model_dir, trained_at=time.time() - 40 * 86400)
        out = ci.predict_carry_decision({"a": 1.0, "b": 2.0, "c": 3.0})
        assert out["shielded"] is True
        assert out["novelty_flag"] == 2
        assert out["eligible"] is False

    def test_fresh_model_proceeds(self, model_dir):
        import time
        _write_meta(model_dir, trained_at=time.time())
        out = ci.predict_carry_decision({"a": 1.0, "b": 2.0, "c": 3.0})
        assert out["shielded"] is False
        assert out["eligible"] is True  # stub q20=20 > 16
        assert out["q20_bps"] == 20.0

    def test_age_override_respected(self, model_dir):
        import time
        _write_meta(model_dir, trained_at=time.time() - 40 * 86400)
        out = ci.predict_carry_decision(
            {"a": 1.0, "b": 2.0, "c": 3.0}, max_model_age_s=10 ** 9)
        assert out["shielded"] is False
        assert out["eligible"] is True

    def test_no_meta_means_no_expiry(self, model_dir):
        # Pre-sidecar artifacts: age unknown -> never expires, finiteness decides.
        out = ci.predict_carry_decision({"a": 1.0, "b": 2.0, "c": 3.0})
        assert out["shielded"] is False
        assert out["eligible"] is True


def test_build_model_metadata_shape(tmp_path):
    """Train-time sidecar builder: pure, dependency-light, honest about
    missing inputs (None hash / 'unknown' version, never fabricated)."""
    from ml.train_carry import MODEL_META_VERSION, build_model_metadata
    data = tmp_path / "dataset.csv"
    data.write_text("a,b\n1,2\n")
    meta = build_model_metadata(
        ["a", "b"], 100, ["BTC", "ETH"], dataset_path=data,
        code_version="deadbee",
    )
    assert meta["meta_version"] == MODEL_META_VERSION
    assert meta["feature_cols"] == ["a", "b"]
    assert meta["n_rows"] == 100
    assert meta["symbols"] == ["BTC", "ETH"]
    assert isinstance(meta["trained_at"], float)
    assert len(meta["dataset_sha256"]) == 64
    assert meta["code_version"] == "deadbee"


def test_build_model_metadata_honest_when_inputs_absent():
    from ml.train_carry import build_model_metadata
    meta = build_model_metadata(
        ["a"], 10, ["BTC"],
        dataset_path="/nonexistent/dataset.csv", code_version=None,
    )
    assert meta["dataset_sha256"] is None
    assert isinstance(meta["code_version"], str)
