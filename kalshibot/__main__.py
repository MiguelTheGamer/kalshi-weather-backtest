"""Command line: python -m kalshibot {backtest,intraday} [options]."""
import argparse
import json
import logging
from datetime import date

from .backtest import run_series, save_calibration, save_summary
from .config import RiskConfig, load_dotenv
from .research import intraday_study

DEFAULT_SERIES = ("KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA")


def main() -> None:
    p = argparse.ArgumentParser(prog="kalshibot")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("backtest", "intraday"):
        s = sub.add_parser(name)
        s.add_argument("--since", type=date.fromisoformat, required=True, help="first day to score, YYYY-MM-DD")
        s.add_argument("--series", nargs="+", default=list(DEFAULT_SERIES))
    sub.choices["intraday"].add_argument("--hours", nargs="+", type=int, default=[11, 13, 15, 17],
                                         help="decision hours, local standard time")
    args = p.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.ERROR)
    cfg = RiskConfig.from_env()

    if args.cmd == "backtest":
        results = [run_series(s, lead, args.since, cfg) for s in args.series for lead in (2, 1)]
        for r in results:
            print(json.dumps(r.summary()))
        save_summary(results)
        save_calibration(results)
    else:
        for s in args.series:
            for h, score in intraday_study(s, args.since, tuple(args.hours), cfg).items():
                print(json.dumps({"series": s, "hour_lst": h, **score.summary()}))


if __name__ == "__main__":
    main()
