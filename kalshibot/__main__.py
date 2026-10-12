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
    ls = sub.add_parser("longshot", help="favorite-longshot test across all non-parlay markets")
    ls.add_argument("--days", type=int, default=120)
    ls.add_argument("--windows", type=int, default=80)
    ls.add_argument("--report-only", action="store_true", help="print data/longshot_results.json")
    mk = sub.add_parser("makers", help="realized maker returns from trade records")
    mk.add_argument("--markets", type=int, default=1500)
    mk.add_argument("--report-only", action="store_true", help="print data/maker_results.json")
    args = p.parse_args()

    if args.cmd == "makers":
        from .makers import print_report, run
        if not args.report_only:
            run(n_markets=args.markets)
        print_report()
        return

    if args.cmd == "longshot":
        from .longshot import print_report, run
        if not args.report_only:
            run(days=args.days, windows=args.windows)
        print_report()
        return

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
