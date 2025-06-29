import os
import math
import requests
from typing import Optional
from datetime import datetime
from urllib.parse import quote
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# LangChain & DeepSeek & Tools
from langchain.agents import AgentType, initialize_agent
from langchain.memory import ConversationBufferMemory
from langchain_deepseek import ChatDeepSeek
from langchain.tools import Tool, StructuredTool

# 知识库相关
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain.chains import RetrievalQA
from langchain.embeddings import HuggingFaceEmbeddings

import pandas as pd

# ----------------------------
# 加载环境变量
# ----------------------------
load_dotenv()
if not os.getenv("DEEPSEEK_API_KEY"):
    print("❌ 警告：未检测到 DEEPSEEK_API_KEY，请检查 .env 文件或系统环境变量设置")
else:
    print("✅ 检测到 DEEPSEEK_API_KEY")

# ----------------------------
# Calculator 工具
# ----------------------------
class CalculatorInput(BaseModel):
    operation: str = Field(description="操作类型: add, subtract, multiply, divide, power, sqrt")
    a: float = Field(description="第一个数字")
    b: Optional[float] = Field(default=None, description="第二个数字 (sqrt 操作时可为空)")

def calculator(operation: str, a: float, b: Optional[float] = None) -> str:
    op = operation.lower()
    try:
        if op == "add":
            return f"{a} + {b} = {a + b}"
        elif op == "subtract":
            return f"{a} - {b} = {a - b}"
        elif op == "multiply":
            return f"{a} * {b} = {a * b}"
        elif op == "divide":
            if b == 0:
                return "错误：不能除以0"
            return f"{a} / {b} = {a / b}"
        elif op == "power":
            return f"{a}^{b} = {a ** b}"
        elif op == "sqrt":
            if a < 0:
                return "错误：不能对负数开平方"
            return f"√{a} = {math.sqrt(a)}"
        else:
            return f"未知操作: {op}"
    except Exception as e:
        return f"计算错误: {str(e)}"

calculator_tool = StructuredTool.from_function(
    func=calculator,
    name="Calculator",
    description="进行数学运算（加减乘除、幂、开方）",
    args_schema=CalculatorInput
)

# ----------------------------
# Weather 工具
# ----------------------------
class WeatherInput(BaseModel):
    city: str = Field(description="城市名称")

def get_weather(city: str) -> str:
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        return "❌ 错误：未配置 OPENWEATHER_API_KEY"
    
    url = f"https://api.openweathermap.org/data/2.5/weather?q={quote(city)}&appid={api_key}&units=metric"
    try:
        response = requests.get(url)
        data = response.json()

        if response.status_code != 200:
            if data.get("message") == "city not found":
                return f"未找到城市：{city}，请尝试使用拼音如 Beijing"
            return f"天气查询失败：{data.get('message', '未知错误')}"

        weather_desc = data["weather"][0]["description"]
        temp = data["main"]["temp"]
        feels_like = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        wind_speed = data["wind"]["speed"]

        return (
            f"📍 {city} 当前天气：\n"
            f"- 天气: {weather_desc}\n"
            f"- 温度: {temp}°C (体感 {feels_like}°C)\n"
            f"- 湿度: {humidity}%\n"
            f"- 风速: {wind_speed} m/s"
        )
    except Exception as e:
        return f"天气查询出错：{str(e)}"

weather_tool = StructuredTool.from_function(
    func=get_weather,
    name="Weather",
    description="查询城市天气",
    args_schema=WeatherInput
)

# ----------------------------
# 知识库工具
# ----------------------------
def setup_knowledge_base_tool(pdf_path: str):
    class KnowledgeInput(BaseModel):
        query: str = Field(description="要查询的知识点")

    if not os.path.exists(pdf_path):
        return StructuredTool.from_function(
            func=lambda x: "❌ 错误：知识库文档不存在。",
            name="KnowledgeBaseQueryTool",
            description="用于从知识库 PDF 文档中回答问题（当前文档未找到）。",
            args_schema=KnowledgeInput
        )

    try:
        loader = PyPDFLoader(pdf_path)
        documents = loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        texts = text_splitter.split_documents(documents)

        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        db = FAISS.from_documents(texts, embeddings)
        retriever = db.as_retriever()
        qa_chain = RetrievalQA.from_chain_type(
            llm=ChatDeepSeek(api_key=os.getenv("DEEPSEEK_API_KEY")),
            chain_type="stuff",
            retriever=retriever
        )

        return StructuredTool.from_function(
            func=qa_chain.run,
            name="KnowledgeBaseQueryTool",
            description="用于从知识库 PDF 文档中回答问题。",
            args_schema=KnowledgeInput
        )
    except Exception as e:
        return StructuredTool.from_function(
            func=lambda x: f"❌ 错误：知识库加载失败: {e}",
            name="KnowledgeBaseQueryTool",
            description="知识库问答工具初始化失败。",
            args_schema=KnowledgeInput
        )


# ----------------------------
# Agent 构建
# ----------------------------
def create_agent():
    llm = ChatDeepSeek(
        model="deepseek-chat",
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        temperature=0.3
    )

    # ✅ 这里要改成你真实的文件名
    pdf_path = "./docs/the_history_of_ship.pdf"
    knowledge_tool = setup_knowledge_base_tool(pdf_path)

    tools = [calculator_tool, weather_tool, knowledge_tool]
    memory = ConversationBufferMemory(
        memory_key="chat_history",
        return_messages=True
    )

    agent = initialize_agent(
        tools=tools,
        llm=llm,
        memory=memory,
        agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
        verbose=True,
        handle_parsing_errors=True
    )
    return agent


# ----------------------------
# CLI 主程序
# ----------------------------
if __name__ == "__main__":
    print("🤖 智能 Agent 工具（DeepSeek + Calculator + Weather + PDF 知识库）")
    print("输入 'exit' 退出程序")

    try:
        agent = create_agent()
        print("✅ Agent 初始化成功！")
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        exit(1)

    while True:
        query = input("\n请输入问题：")
        if query.strip().lower() == "exit":
            print("👋 再见！")
            break

        try:
            result = agent.invoke({"input": query})
            print(f"\n🧠 回复结果:\n{result['output']}")
        except Exception as e:
            print(f"\n❌ 错误：{e}")
            args_schema=KnowledgeInput
        )
