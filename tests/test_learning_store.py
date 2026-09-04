"""I12 regression: learning-state store persists model weights + eval
dataset + profiles with a committed Merkle root per cycle."""
import pickle
import tempfile
import pytest

from src.learning_store import (
    LearningStore,
    LearningState,
    save_learning_state,
    load_learning_state,
    merkle_root,
)


def _dummy_model():
    class _M:
        pass
    return _M()


class TestLearningStoreBasics:
    def test_save_and_load_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            store = LearningStore(d)
            store.save(LearningState(
                cycle_id="c1",
                primary_model=pickle.dumps({"w": [1, 2]}),
                meta_model=pickle.dumps({"m": 1}),
                feature_names=["f1", "f2"],
                eval_dataset_hash="abc123",
                eval_dataset_path="/data/eval.csv",
                curator_profile={"profile": "standard"},
                ml_profiles={"gate": {"threshold": 0.5}},
                validation_report={"calmar": 1.5, "passed": True},
                train_symbols=["BTC", "ETH"],
                n_train_rows=10000,
            ))
            loaded = store.load("c1")
            assert loaded is not None
            assert loaded.feature_names == ["f1", "f2"]
            assert loaded.eval_dataset_hash == "abc123"
            assert loaded.curator_profile == {"profile": "standard"}

    def test_model_pickles_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = LearningStore(d)
            store.save(LearningState(
                cycle_id="c1",
                primary_model=pickle.dumps({"weights": [1, 2, 3]}),
                meta_model=pickle.dumps({"m": 1}),
                feature_names=["f1"],
                eval_dataset_hash="h1",
                eval_dataset_path="/data/eval.csv",
                train_symbols=["BTC"],
                n_train_rows=100,
            ))
            loaded = store.load("c1")
            assert loaded is not None
            loaded_model = pickle.loads(loaded.primary_model)
            assert loaded_model["weights"] == [1, 2, 3]

    def test_cycle_root_merklizes_states(self):
        with tempfile.TemporaryDirectory() as d:
            store = LearningStore(d)
            for i in range(3):
                store.save(LearningState(
                    cycle_id=f"c{i}",
                    feature_names=[f"f{i}"],
                    eval_dataset_hash=f"h{i}",
                    eval_dataset_path="/data/eval.csv",
                    train_symbols=["BTC"],
                    n_train_rows=1000,
                ))
            root = store.compute_root()
            assert len(root) == 32
            # Same order -> same root
            assert store.compute_root() == store.compute_root()

    def test_empty_store_zero_root(self):
        with tempfile.TemporaryDirectory() as d:
            store = LearningStore(d)
            root = store.compute_root()
            assert root == b"\x00" * 32

    def test_load_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            store = LearningStore(d)
            assert store.load("nonexistent") is None


class TestBackwardsCompat:
    def test_save_load_helpers(self):
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/cycle_1"
            save_learning_state(
                path, "cycle_1",
                primary_model={"w": [1]},
                meta_model=None,
                feature_names=["f1"],
                eval_dataset_hash="abc",
                eval_dataset_path="/data/eval.csv",
                curator_profile={},
                ml_profiles={},
                validation_report={},
                train_symbols=["BTC"],
                n_train_rows=100,
            )
            state = load_learning_state(d, "cycle_1")
            assert state is not None
            assert state.feature_names == ["f1"]
            assert state.eval_dataset_hash == "abc"
            # ML_DEGRADATIONS is from signals module, unrelated to learning store


class TestMerkleRoot:
    def test_deterministic(self):
        leaves = [b"a", b"b", b"c"]
        assert merkle_root([b"a", b"b", b"c"]) == merkle_root([b"a", b"b", b"c"])

    def test_empty_tree_zero(self):
        assert merkle_root([]) == b"\x00" * 32

    def test_single_leaf_is_leaf(self):
        assert merkle_root([b"a"]) == b"a"

    def test_order_matters(self):
        assert merkle_root([b"a", b"b"]) != merkle_root([b"b", b"a"])