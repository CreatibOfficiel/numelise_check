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


import os
import yaml

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CONSENT_MANAGERS_FILE = f"{MODULE_DIR}/assets/consent_managers.yml"


def get_consent_managers():
    with open(CONSENT_MANAGERS_FILE, "r") as f:
        data = yaml.safe_load(f)
        return data
