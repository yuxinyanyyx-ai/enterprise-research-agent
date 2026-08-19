from src.tools.dmf_tools import search_dmf

def main():
    result = search_dmf.invoke(
        {
            "dmf_no": "",
            "applicant_name": "",
            "ingredients": [
                "Ibuprofen",
                "Metformin",
            ],
        }
    )

    print("\n========== Tool 返回结果 ==========")
    print(result)


if __name__ == "__main__":
    main()