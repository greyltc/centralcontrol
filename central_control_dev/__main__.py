import os,sys

def main():
    if ('CENTRAL_GUI_CONTROL' in os.environ) or ('-gui' in sys.argv[0]):
        from central_control.gui.gui import gui
        g = gui()
        g.run()

    else:
        from central_control.cli import cli
        c = cli()
        c.run()

if __name__ == "__main__":
    main()
