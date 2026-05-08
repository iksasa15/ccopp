#!/usr/bin/env python3
"""
Train baseline threat models from local datasets.

Outputs:
- data/models/network_threat_model.joblib
- data/models/memory_threat_model.joblib
- data/models/training_metrics.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from joblib import dump
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = ROOT / "data" / "datasets"
MODELS_DIR = ROOT / "data" / "models"


def _build_binary_label(series: pd.Series) -> pd.Series:
    return (series.astype(str).str.lower() != "benign").astype(int)


def _sanitize_numeric_frame(df: pd.DataFrame) -> pd.DataFrame:
    clean = df.replace([np.inf, -np.inf], np.nan)
    # Guard against extremely large values that can break some estimators.
    return clean.clip(lower=-1e15, upper=1e15)


def _build_pipeline(feature_columns: list[str]) -> Pipeline:
    numeric_preproc = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler(with_mean=False)),
        ]
    )
    preprocessor = ColumnTransformer(
        transformers=[("num", numeric_preproc, feature_columns)],
        remainder="drop",
    )
    classifier = SGDClassifier(
        loss="log_loss",
        alpha=1e-4,
        max_iter=1000,
        tol=1e-3,
        random_state=42,
        class_weight="balanced",
    )
    return Pipeline(steps=[("prep", preprocessor), ("clf", classifier)])


def _metrics(y_true: pd.Series, y_pred: pd.Series) -> dict[str, float]:
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
    }


def train_cic_ids2018(max_rows: int | None = None) -> dict[str, Any]:
    csv_path = DATASETS_DIR / "cic_ids2018" / "02-15-2018.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing dataset: {csv_path}")

    df = pd.read_csv(csv_path, nrows=max_rows)
    if "Label" not in df.columns:
        raise ValueError("CIC dataset missing 'Label' column")

    y = _build_binary_label(df["Label"])
    numeric_features = [
        c for c in df.columns if c != "Label" and pd.api.types.is_numeric_dtype(df[c])
    ]
    X = _sanitize_numeric_frame(df[numeric_features].copy())

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    pipeline = _build_pipeline(numeric_features)
    pipeline.fit(X_train, y_train)
    preds = pipeline.predict(X_test)
    metrics = _metrics(y_test, preds)

    model_path = MODELS_DIR / "network_threat_model.joblib"
    dump(
        {
            "pipeline": pipeline,
            "feature_columns": numeric_features,
            "label_name": "is_attack",
            "source": str(csv_path),
        },
        model_path,
    )

    return {
        "dataset": "cic_ids2018",
        "rows_used": int(len(df)),
        "features": len(numeric_features),
        "model_path": str(model_path),
        "metrics": metrics,
    }


def train_cic_malmem(max_rows: int | None = None) -> dict[str, Any]:
    csv_path = DATASETS_DIR / "cic_malmem" / "Obfuscated-MalMem2022.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing dataset: {csv_path}")

    df = pd.read_csv(csv_path, nrows=max_rows)
    if "Category" not in df.columns:
        raise ValueError("MalMem dataset missing 'Category' column")

    y = _build_binary_label(df["Category"])
    numeric_features = [
        c for c in df.columns if c != "Category" and pd.api.types.is_numeric_dtype(df[c])
    ]
    X = _sanitize_numeric_frame(df[numeric_features].copy())

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    pipeline = _build_pipeline(numeric_features)
    pipeline.fit(X_train, y_train)
    preds = pipeline.predict(X_test)
    metrics = _metrics(y_test, preds)

    model_path = MODELS_DIR / "memory_threat_model.joblib"
    dump(
        {
            "pipeline": pipeline,
            "feature_columns": numeric_features,
            "label_name": "is_malware",
            "source": str(csv_path),
        },
        model_path,
    )

    return {
        "dataset": "cic_malmem",
        "rows_used": int(len(df)),
        "features": len(numeric_features),
        "model_path": str(model_path),
        "metrics": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train local threat models")
    parser.add_argument(
        "--max-rows-cic",
        type=int,
        default=250000,
        help="Max rows from CIC-IDS2018 for faster local training",
    )
    parser.add_argument(
        "--max-rows-malmem",
        type=int,
        default=None,
        help="Max rows from CIC-MalMem (default: all rows)",
    )
    args = parser.parse_args()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    cic_result = train_cic_ids2018(max_rows=args.max_rows_cic)
    malmem_result = train_cic_malmem(max_rows=args.max_rows_malmem)

    summary = {
        "trained_at": pd.Timestamp.utcnow().isoformat(),
        "results": [cic_result, malmem_result],
    }

    metrics_path = MODELS_DIR / "training_metrics.json"
    metrics_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nSaved metrics: {metrics_path}")


if __name__ == "__main__":
    main()

