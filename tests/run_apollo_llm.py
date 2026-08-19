from src.llm.apollo import create_apollo_llm


def main():
    print("正在连接 Apollo...")

    llm = create_apollo_llm()

    print("Apollo LLM 创建成功")

    response = llm.invoke(
        "只回复下面这句话，不要添加其他内容：Apollo connection successful"
    )

    print("模型返回：")
    print(response.content)


if __name__ == "__main__":
    main()