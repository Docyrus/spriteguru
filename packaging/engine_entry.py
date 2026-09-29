import multiprocessing

if __name__ == "__main__":
    # eval's process-pool workers re-enter this binary; without this they run the CLI with Python's
    # own flags ("No such option: -B") and the pool breaks.
    multiprocessing.freeze_support()
    from spriteguru.cli import main

    main()
