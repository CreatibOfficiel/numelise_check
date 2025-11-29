import asyncio
import os
import json
import logging
import argparse
import sys
from consentcrawl import utils, blocklists, audit_crawl, audit_schemas


def cli():
    parser = argparse.ArgumentParser()

    parser.add_argument("url", help="URL or file with URLs to test")
    parser.add_argument(
        "--debug", default=False, action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--headless",
        default=True,
        type=utils.string_to_boolean,
        const=False,
        nargs="?",
        help="Run browser in headless mode (yes/no)",
    )
    parser.add_argument(
        "--screenshot",
        default=False,
        action="store_true",
        help="Take screenshots of each page before and after consent is given (if consent manager is detected)",
    )
    parser.add_argument(
        "--bootstrap",
        default=False,
        action="store_true",
        help="Force bootstrap (refresh) of blocklists",
    )
    parser.add_argument(
        "--batch_size",
        "-b",
        default=15,
        type=int,
        help="Number of URLs (and browser windows) to run in each batch. Default: 15, increase or decrease depending on your system capacity.",
    )
    parser.add_argument(
        "--show_output",
        "-o",
        default=False,
        action="store_true",
        help="Show output of the last results in terminal (max 25 results)",
    )
    parser.add_argument(
        "--db_file",
        "-db",
        default="crawl_results.db",
        help="Path to crawl results and blocklist database",
    )
    parser.add_argument(
        "--blocklists", "-bf", default=None, help="Path to custom blocklists file"
    )
    parser.add_argument(
        "--mode",
        default="audit",
        choices=["crawl", "audit"],
        help="Mode: 'audit' (default) for CMP UI exploration, 'crawl' for consent impact analysis"
    )
    parser.add_argument(
        "--output_dir",
        default="./audit_results",
        help="Directory for audit mode JSON output files (audit mode only, default: ./audit_results)"
    )
    parser.add_argument(
        "--max_ui_depth",
        default=3,
        type=int,
        help="Maximum depth for UI exploration (audit mode only, default: 3)"
    )
    parser.add_argument(
        "--timeout_banner",
        default=10000,
        type=int,
        help="Timeout for banner detection in milliseconds (audit mode only, default: 10000)"
    )
    parser.add_argument(
        "--timeout_modal",
        default=15000,
        type=int,
        help="Timeout for modal operations in milliseconds (audit mode only, default: 15000)"
    )

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    if not args.db_file.endswith(".db"):
        args.db_file = args.db_file + ".db"

    if args.blocklists != None:
        if not os.path.isfile(args.blocklists):
            logging.error(f"Blocklists file not found: {args.blocklists}")
            sys.exit(1)

        if not any(
            [args.blocklists.endswith(".yaml"), args.blocklists.endswith(".yml")]
        ):
            logging.error(f"Blocklists file must be a YAML file: {args.blocklists}")
            sys.exit(1)

    if not os.path.isdir("screenshots") and args.screenshot == True:
        os.mkdir("screenshots")

    # List of URLs to test
    if args.url.endswith(".txt"):
        with open(args.url, "r") as f:
            urls = list(
                set(
                    [
                        l.strip().lower()
                        for l in set(f.readlines())
                        if len(l) > 0 and not l.startswith("#")
                    ]
                )
            )

    elif args.url != "":
        urls = args.url.split(",")
    else:
        logging.error("No URL or valid .txt file with URLs to test")

    if args.mode == "audit":
        # Audit mode (default behavior)
        if not os.path.isdir(args.output_dir):
            os.makedirs(args.output_dir)

        audit_config = audit_schemas.AuditConfig(
            max_ui_depth=args.max_ui_depth,
            timeout_banner=args.timeout_banner,
            timeout_modal=args.timeout_modal,
            languages=['en', 'fr'],
            support_shadow_dom=True,
            support_nested_iframes=True,
        )

        results = asyncio.run(
            audit_crawl.audit_batch(
                urls=urls,
                batch_size=args.batch_size,
                config=audit_config,
                headless=args.headless,
                results_db_file=args.db_file,
                output_dir=args.output_dir,
                screenshot=args.screenshot,
            )
        )

        if args.show_output and len(results) < 25:
            sys.stdout.write(json.dumps(results, indent=2))

    else:
        logging.error("Only 'audit' mode is supported in this version.")
        sys.exit(1)

    sys.exit(0)
