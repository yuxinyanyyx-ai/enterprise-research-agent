from src.competitive_intelligence.cortellis.client import CortellisClient


def main():
    with CortellisClient() as client:
        result = client.search_drugs("baricitinib")

        print("count:", result.get("count"))

        for item in result.get("results", []):
            print(
                item.get("drugId"),
                item.get("drugName"),
            )


if __name__ == "__main__":
    main()