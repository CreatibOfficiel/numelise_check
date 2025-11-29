import logging
import os
import re
import yaml
from typing import List, Tuple


def batch(iterable, n=1):
    """
    Turn any iterable into a generator of batches of batch size n
    from: https://stackoverflow.com/a/8290508
    """
    l = len(iterable)
    for ndx in range(0, l, n):
        yield iterable[ndx : min(ndx + n, l)]


def string_to_boolean(v):
    """
    Convert many string options to a boolean value. Useful for argparsing.
    From: https://stackoverflow.com/a/43357954/5761491
    """
    if isinstance(v, bool):
        return v
    if v.lower() in ("yes", "true", "t", "y", "1"):
        return True
    elif v.lower() in ("no", "false", "f", "n", "0"):
        return False
    else:
        raise argparse.ArgumentTypeError("Boolean value expected.")


MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CONSENT_MANAGERS_FILE = f"{MODULE_DIR}/assets/consent_managers.yml"


def get_consent_managers():
    with open(CONSENT_MANAGERS_FILE, "r") as f:
        data = yaml.safe_load(f)
        return data


def process_network_requests(
    requests: List[str], 
    domain: str, 
    tracking_domains: set
) -> Tuple[List[str], List[str]]:
    """
    Process captured network requests to identify third-party and tracking domains.
    
    Args:
        requests: List of captured request URLs
        domain: Main domain name to filter out
        tracking_domains: Set of known tracking domains
        
    Returns:
        Tuple of (third_party_domains, tracking_domains_found)
    """
    try:
        # Filter third-party requests (not from main domain)
        third_party_requests = [url for url in requests if domain not in url]
        
        # Extract unique domains from URLs
        third_party_domains = set()
        for url in third_party_requests:
            match = re.search(r"https?://(?:www\.)?([^/]+)", url)
            if match:
                third_party_domains.add(match.group(1))
        
        # Identify tracking domains
        tracking = [
            d for d in third_party_domains 
            if any(tracker in d for tracker in tracking_domains)
        ]
        
        logging.info(
            f"Found {len(third_party_domains)} third-party domains, "
            f"{len(tracking)} trackers"
        )
        
        return list(third_party_domains), tracking
        
    except Exception as e:
        logging.warning(f"Failed to process network requests: {e}")
        return [], []
