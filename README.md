# Simple AI Chat

一个最小可用的 AI 聊天网站，支持：

- 输入消息并和 AI 对话
- 清除当前对话历史
- 新建会话
- 切换模型
- 切换 `Global / CN` API URL
- `Enter` 发送，`Shift + Enter` 换行

默认 URL 为 `SILICONFLOW_URL_CN`。

## 环境

建议在你提到的 `langgraphenv` 环境里安装依赖：

```powershell
conda activate langgraphenv
pip install -r requirements.txt
```

## 环境变量

项目使用 `dotenv`，读取根目录 `.env`：

```env
SILICONFLOW_API_KEY=your_key
SILICONFLOW_URL=https://api.siliconflow.com/v1
SILICONFLOW_URL_CN=https://api.siliconflow.cn/v1
```

## 启动

```powershell
python app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```
