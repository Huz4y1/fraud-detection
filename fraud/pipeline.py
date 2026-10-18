from fraud import download, medallion, model, warehouse


def main():
    download.main()
    medallion.main()
    warehouse.main()
    model.main()


if __name__ == "__main__":
    main()
