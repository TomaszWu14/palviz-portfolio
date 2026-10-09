"""Próbka prawdziwego eksportu EWM (B0, przejścia 07/08/34/38/48/54) dla testów części 1."""
from pathlib import Path

SAMPLE = Path(__file__).with_name("data") / "ewm_b0_sample.csv"
# Liczba gniazd rzędów w modelu z rysunku (regaly_z_rysunku.csv, kolumna n_bays).
SAMPLE_N_BAYS = {"07": 39, "08": 39, "34": 42, "38": 41, "48": 36, "54": 34}


def load_sample():
    """[(kod, typ EWM)] z pliku próbki (pomija komentarz i nagłówek)."""
    lines = [ln for ln in SAMPLE.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    return [tuple(ln.split(";")) for ln in lines[1:]]
