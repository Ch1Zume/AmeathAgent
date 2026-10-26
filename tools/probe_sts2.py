"""Read-only diagnostics for Ameath's installed game bridge."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ameath.game import SpireBridge, choices_for


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    snapshot = SpireBridge(args.endpoint).snapshot()
    print(json.dumps({"connected": True, "service": snapshot.health.get("service"),
                      "mod_version": snapshot.health.get("mod_version"), "game_version": snapshot.health.get("game_version"),
                      "compatibility": snapshot.health.get("status"), "screen": snapshot.screen,
                      "legal_choices": len(choices_for(snapshot)), "model_calls": 0}, ensure_ascii=True))


if __name__ == "__main__":
    main()
