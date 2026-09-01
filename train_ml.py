#!/usr/bin/env python3
"""
TARS ML Model Training Script

Trains a small neural network (17→8→1) matching the TEE contract architecture,
using historical OHLCV data to predict next-candle direction.

Architecture: 17 features → 8 hidden (ReLU) → 1 output (sigmoid) = 153 weights
Exports weights in Rust const format for t3n/contract/src/ml.rs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler


# ─── Feature Engineering (must match extract17Features in signal.ts) ───

def compute_17_features(candles: np.ndarray) -> np.ndarray:
    """
    Compute 17 features from OHLCV candles.
    
    candles: shape (N, 5) = [open, high, low, close, volume]
    Returns: (N-1, 17) features aligned with next-candle targets
    """
    if len(candles) < 30:
        raise ValueError("Need at least 30 candles")
    
    o, h, l, c, v = candles[:, 0], candles[:, 1], candles[:, 2], candles[:, 3], candles[:, 4]
    n = len(c)
    
    features = np.zeros((n - 1, 17), dtype=np.float32)
    
    for i in range(1, n):
        # Window for rolling stats
        win_start = max(0, i - 20)
        recent_c = c[win_start:i+1]
        recent_v = v[win_start:i+1]
        
        # 1. Returns (1h, 4h, 12h, 24h)
        features[i-1, 0] = (c[i] - c[i-1]) / c[i-1]  # 1h return
        features[i-1, 1] = (c[i] - c[max(0, i-4)]) / c[max(0, i-4)]  # 4h
        features[i-1, 2] = (c[i] - c[max(0, i-12)]) / c[max(0, i-12)]  # 12h
        features[i-1, 3] = (c[i] - c[max(0, i-24)]) / c[max(0, i-24)]  # 24h
        
        # 4-7. Moving average ratios (MA5/MA20, MA20/MA50, etc.)
        ma5 = np.mean(c[max(0, i-5):i+1])
        ma20 = np.mean(c[max(0, i-20):i+1])
        ma50 = np.mean(c[max(0, i-50):i+1])
        features[i-1, 4] = (ma5 - ma20) / ma20
        features[i-1, 5] = (ma20 - ma50) / ma50
        features[i-1, 6] = (c[i] - ma5) / ma5
        features[i-1, 7] = (c[i] - ma20) / ma20
        
        # 8. RSI (14)
        deltas = np.diff(c[max(0, i-14):i+1])
        gains = np.where(deltas > 0, deltas, 0).mean()
        losses = np.where(deltas < 0, -deltas, 0).mean()
        rs = gains / losses if losses > 0 else 100
        features[i-1, 8] = (100 - 100/(1+rs)) / 100  # normalized RSI
        
        # 9. Volatility (20-period std of returns)
        recent_rets = np.diff(recent_c) / recent_c[:-1]
        features[i-1, 9] = np.std(recent_rets) * np.sqrt(24)  # annualized
        
        # 10. Volume ratio (current vs 20-period avg)
        vol_avg = np.mean(recent_v)
        features[i-1, 10] = v[i] / vol_avg if vol_avg > 0 else 1.0
        
        # 11. Volume trend (5-period vs 20-period)
        vol_5 = np.mean(v[max(0, i-5):i+1])
        vol_20 = np.mean(v[max(0, i-20):i+1])
        features[i-1, 11] = vol_5 / vol_20 if vol_20 > 0 else 1.0
        
        # 12. High-low range ratio (volatility proxy)
        hl_range = (h[max(0, i-20):i+1] - l[max(0, i-20):i+1]).mean()
        features[i-1, 12] = hl_range / c[i]
        
        # 13. Price position in 20-period range
        low_20 = np.min(l[max(0, i-20):i+1])
        high_20 = np.max(h[max(0, i-20):i+1])
        features[i-1, 13] = (c[i] - low_20) / (high_20 - low_20) if high_20 > low_20 else 0.5
        
        # 14. Momentum (close - close 10 periods ago) / close 10 ago
        features[i-1, 14] = (c[i] - c[max(0, i-10)]) / c[max(0, i-10)]
        
        # 15. Volume-weighted momentum
        vwap_5 = np.sum(c[max(0, i-5):i+1] * v[max(0, i-5):i+1]) / np.sum(v[max(0, i-5):i+1])
        features[i-1, 15] = (c[i] - vwap_5) / vwap_5 if vwap_5 > 0 else 0.0
        
        # 16. Trend strength (ADX-like)
        plus_dm = np.maximum(h[1:i+1] - h[:i], 0)
        minus_dm = np.maximum(l[:i] - l[1:i+1], 0)
        tr = np.maximum(h[1:i+1] - l[1:i+1], np.maximum(
            np.abs(h[1:i+1] - c[:i]), np.abs(l[1:i+1] - c[:i])
        ))
        atr = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
        features[i-1, 16] = (np.mean(plus_dm[-14:]) - np.mean(minus_dm[-14:])) / atr if atr > 0 else 0.0
    
    return features


def create_targets(candles: np.ndarray, horizon: int = 1) -> np.ndarray:
    """
    Create binary targets: 1 if next N candles close higher, 0 otherwise.
    """
    c = candles[:, 3]
    targets = np.zeros(len(c) - horizon, dtype=np.float32)
    for i in range(len(c) - horizon):
        targets[i] = 1.0 if c[i + horizon] > c[i] else 0.0
    return targets


# ─── Model Definition (matches Rust 17→8→1) ───

class TinyNN(nn.Module):
    """17 → 8 (ReLU) → 1 (Sigmoid)"""
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(17, 8)
        self.fc2 = nn.Linear(8, 1)
        self.relu = nn.ReLU()
        self.sigmoid = nn.Sigmoid()
    
    def forward(self, x):
        x = self.relu(self.fc1(x))
        x = self.sigmoid(self.fc2(x))
        return x


def export_weights_rust(model: TinyNN, scaler: StandardScaler, output_path: Path) -> None:
    """Export weights in Rust const array format for ml.rs"""
    fc1_w = model.fc1.weight.data.cpu().numpy().T  # (17, 8)
    fc1_b = model.fc1.bias.data.cpu().numpy()      # (8,)
    fc2_w = model.fc2.weight.data.cpu().numpy().T  # (8, 1)
    fc2_b = model.fc2.bias.data.cpu().numpy()      # (1,)
    
    # Flatten W1 in row-major (matches Rust: W1[j * 17 + i])
    w1_flat = fc1_w.flatten()  # shape (136,) = 17*8
    
    # Format as Rust arrays
    rust_output = f"""// Auto-generated by train_ml.py — DO NOT EDIT MANUALLY
// Architecture: 17 -> 8 (ReLU) -> 1 (Sigmoid) = 153 params
// Trained on: {np.datetime64('today')}
// Scaler: mean={scaler.mean_.tolist()}, scale={scaler.scale_.tolist()}

pub const W1: [f32; 17 * 8] = [
"""
    # Write in 17-per-row chunks for readability
    for j in range(8):
        row = w1_flat[j*17:(j+1)*17]
        rust_output += "    " + ",  ".join(f"{v:+.6f}" for v in row) + ",\n"
    
    rust_output += f"""];

pub const B1: [f32; 8] = [{",  ".join(f"{v:+.6f}" for v in fc1_b)}];

pub const W2: [f32; 8] = [{",  ".join(f"{v:+.6f}" for v in fc2_w.flatten())}];

pub const B2: f32 = {fc2_b[0]:+.6f};
"""
    
    output_path.write_text(rust_output)
    print(f"Exported Rust weights to {output_path}")


def export_scaler_json(scaler: StandardScaler, output_path: Path) -> None:
    """Export scaler for Python inference"""
    data = {
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
    }
    output_path.write_text(json.dumps(data, indent=2))
    print(f"Exported scaler to {output_path}")


# ─── Data Loading ───

def load_candles(data_path: str) -> np.ndarray:
    """Load OHLCV candles from JSON or CSV"""
    path = Path(data_path)
    if path.suffix == ".json":
        with open(path) as f:
            data = json.load(f)
        # Expect: [{"o": ..., "h": ..., "l": ..., "c": ..., "v": ..., "t": ...}, ...]
        candles = np.array([
            [d["o"], d["h"], d["l"], d["c"], d["v"]] for d in data
        ], dtype=np.float32)
    elif path.suffix == ".csv":
        import pandas as pd
        df = pd.read_csv(path)
        # Expect columns: open, high, low, close, volume
        candles = df[["open", "high", "low", "close", "volume"]].values.astype(np.float32)
    else:
        raise ValueError(f"Unsupported format: {path.suffix}")
    
    # Ensure sorted by time
    return candles


# ─── Training Loop ───

def train(
    data_path: str,
    epochs: int = 100,
    batch_size: int = 256,
    lr: float = 1e-3,
    val_split: float = 0.2,
    device: str = "cpu",
    output_dir: str = ".",
) -> Tuple[TinyNN, StandardScaler]:
    
    print(f"Loading data from {data_path}...")
    candles = load_candles(data_path)
    print(f"Loaded {len(candles)} candles")
    
    # Compute features and targets
    X = compute_17_features(candles)
    y = create_targets(candles, horizon=1)
    
    # Align
    min_len = min(len(X), len(y))
    X, y = X[:min_len], y[:min_len]
    print(f"Features: {X.shape}, Targets: {y.shape}, Positive rate: {y.mean():.3f}")
    
    # Scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # Train/val split (temporal: last val_split for validation)
    split_idx = int(len(X_scaled) * (1 - val_split))
    X_train, X_val = X_scaled[:split_idx], X_scaled[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]
    
    # Convert to tensors
    X_train_t = torch.FloatTensor(X_train).to(device)
    y_train_t = torch.FloatTensor(y_train).unsqueeze(1).to(device)
    X_val_t = torch.FloatTensor(X_val).to(device)
    y_val_t = torch.FloatTensor(y_val).unsqueeze(1).to(device)
    
    # Model
    model = TinyNN().to(device)
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=10, factor=0.5)
    
    # Training
    best_val_loss = float('inf')
    patience_counter = 0
    
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(len(X_train_t))
        epoch_loss = 0.0
        
        for i in range(0, len(X_train_t), batch_size):
            idx = perm[i:i+batch_size]
            xb, yb = X_train_t[idx], y_train_t[idx]
            
            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item() * len(xb)
        
        epoch_loss /= len(X_train_t)
        
        # Validation
        model.eval()
        with torch.no_grad():
            val_pred = model(X_val_t)
            val_loss = criterion(val_pred, y_val_t).item()
            
            # Accuracy
            val_acc = ((val_pred > 0.5) == (y_val_t > 0.5)).float().mean().item()
        
        scheduler.step(val_loss)
        
        if (epoch + 1) % 10 == 0:
            print(f"Epoch {epoch+1}/{epochs}: train_loss={epoch_loss:.4f}, val_loss={val_loss:.4f}, val_acc={val_acc:.3f}")
        
        # Early stopping
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            best_state = model.state_dict().copy()
        else:
            patience_counter += 1
            if patience_counter >= 20:
                print(f"Early stopping at epoch {epoch+1}")
                break
    
    # Load best model
    model.load_state_dict(best_state)
    
    # Final eval
    model.eval()
    with torch.no_grad():
        val_pred = model(X_val_t)
        val_acc = ((val_pred > 0.5) == (y_val_t > 0.5)).float().mean().item()
        val_auc = roc_auc_score(y_val, val_pred.cpu().numpy()) if len(np.unique(y_val)) > 1 else 0.5
    
    print(f"\nFinal: val_loss={best_val_loss:.4f}, val_acc={val_acc:.3f}, val_auc={val_auc:.3f}")
    
    # Export
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    export_weights_rust(model, scaler, output_dir / "ml_weights.rs")
    export_scaler_json(scaler, output_dir / "scaler.json")
    
    # Also export PyTorch model for Python inference
    torch.save({
        'model_state_dict': model.state_dict(),
        'scaler_mean': scaler.mean_,
        'scaler_scale': scaler.scale_,
    }, output_dir / "model.pt")
    print(f"Saved PyTorch model to {output_dir / 'model.pt'}")
    
    return model, scaler


def roc_auc_score(y_true, y_score):
    """Simple AUC without sklearn dependency"""
    from sklearn.metrics import roc_auc_score as sk_auc
    return sk_auc(y_true, y_score)


def main():
    parser = argparse.ArgumentParser(description="Train TARS ML model")
    parser.add_argument("data", help="Path to OHLCV data (JSON or CSV)")
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs")
    parser.add_argument("--batch-size", type=int, default=256, help="Batch size")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate")
    parser.add_argument("--device", default="cpu", help="Device (cpu/cuda)")
    parser.add_argument("--output", default="ml_output", help="Output directory")
    args = parser.parse_args()
    
    train(
        data_path=args.data,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        device=args.device,
        output_dir=args.output,
    )


if __name__ == "__main__":
    main()