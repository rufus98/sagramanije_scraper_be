"""
Calcolo distanza in linea d'aria tra due coordinate (formula di Haversine).
"""
from math import radians, sin, cos, sqrt, atan2, tan
import requests
import time


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza in km tra due punti sulla superficie terrestre."""
    R = 6371.0  # raggio terrestre medio in km

    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)

    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c * 1.4
