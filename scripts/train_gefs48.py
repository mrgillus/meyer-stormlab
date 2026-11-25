"""Fit only the new +48h model, preserving all existing trained artifacts."""
import json
from pathlib import Path
import regional_learning as learn
ROOT=Path(__file__).resolve().parents[1]
learn.ARCH=ROOT/"data/gefs48"
learn.OUT=ROOT/"data/regional_learning48"
learn.LEADS=(48,)
if __name__=="__main__":
    learn.main()
