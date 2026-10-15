"""
Defect classification using classical computer vision rules or an optional ONNX model.
"""

import logging
from typing import Optional, Tuple

import numpy as np

from xbake.config import InspectionConfig
from xbake.models import DefectType
from xbake.vision.detector import RawDetection

logger = logging.getLogger(__name__)

# Output order expected from ONNX models
ONNX_CLASSES = [DefectType.GOOD, DefectType.BURNT, DefectType.UNDERCOOKED, DefectType.DEFORMED]


class ItemClassifier:
    """
    Classifies detected products into Good, Burnt, Undercooked, or Deformed.
    """

    def __init__(self, config: InspectionConfig, onnx_model_path: Optional[str] = None):
        self.config = config
        self.onnx_model_path = onnx_model_path or config.onnx_model_path or None
        self._onnx_session = None

        if self.onnx_model_path:
            try:
                import onnxruntime as ort

                self._onnx_session = ort.InferenceSession(self.onnx_model_path)
                logger.info(f"Loaded ONNX classification model from {self.onnx_model_path}")
            except Exception as e:
                logger.warning(f"Could not load ONNX model ({e}), using classical CV classifier")

    @property
    def uses_onnx(self) -> bool:
        return self._onnx_session is not None

    def classify(self, detection: RawDetection) -> Tuple[DefectType, float]:
        """
        Classifies a raw candidate detection.
        Returns (DefectType, confidence_score [0.0 - 1.0]).
        """
        if self._onnx_session is not None and getattr(detection.center_roi, "size", 0) > 0:
            return self._classify_onnx(detection.center_roi)

        # 1. Geometry: misshapen, folded, or broken product
        if (
            detection.circularity < self.config.min_circularity
            or detection.aspect_ratio < self.config.min_aspect_ratio
        ):
            confidence = max(0.5, 1.0 - detection.circularity)
            return DefectType.DEFORMED, round(min(1.0, confidence), 2)

        # 2. Burnt: mean intensity inside the burnt window (darker = more confident)
        avg = detection.avg_color
        if self.config.m_color <= avg <= self.config.M_color:
            burn_range = max(1, self.config.M_color - self.config.m_color)
            conf = 1.0 - ((avg - self.config.m_color) / burn_range) * 0.4
            return DefectType.BURNT, round(max(0.60, min(0.99, conf)), 2)

        # 3. Undercooked: excessively pale dough
        if avg >= self.config.undercooked_min_color:
            conf = min(0.95, 0.65 + ((avg - self.config.undercooked_min_color) / 60.0))
            return DefectType.UNDERCOOKED, round(conf, 2)

        return DefectType.GOOD, 0.95

    def _classify_onnx(self, roi: np.ndarray) -> Tuple[DefectType, float]:
        """Inference with an ONNX image classifier (input NCHW float32 224x224, RGB 0-1)."""
        try:
            import cv2

            resized = cv2.resize(roi, (224, 224))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            blob = rgb.astype(np.float32) / 255.0
            blob = np.expand_dims(np.transpose(blob, (2, 0, 1)), axis=0)

            input_name = self._onnx_session.get_inputs()[0].name
            preds = np.asarray(self._onnx_session.run(None, {input_name: blob})[0][0], dtype=np.float64)
            exp_p = np.exp(preds - np.max(preds))
            probs = exp_p / exp_p.sum()
            idx = int(np.argmax(probs))
            if idx < len(ONNX_CLASSES):
                return ONNX_CLASSES[idx], round(float(probs[idx]), 3)
        except Exception as e:
            logger.debug(f"ONNX inference failed: {e}")

        return DefectType.UNKNOWN, 0.0
