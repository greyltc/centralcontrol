import sys

from centralcontrol import CentralControl


def main():
    cc = CentralControl()
    cc.cli()
    cc.run()
    sys.exit(cc.exitcode)


if __name__ == "__main__":
    main()
