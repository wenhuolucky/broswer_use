# Makefile —— 常用开发/运维命令封装
# 用法：make <目标>，直接 make 或 make help 查看全部目标。

# 可被覆盖的变量：make run PORT=9000
HOST ?= 127.0.0.1
PORT ?= 8833
APP  ?= app.server:app

.DEFAULT_GOAL := help

.PHONY: help sync install browser run dev test lint fmt docker-up docker-down docker-logs clean

help: ## 显示本帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

sync: ## 创建 .venv 并按 uv.lock 安装依赖
	uv sync

install: sync browser ## 一键初始化：依赖 + 浏览器内核

browser: ## 安装 Playwright Chromium（首次必需）
	uv run playwright install chromium

run: ## 本地启动服务（HOST/PORT/APP 可覆盖）
	uv run uvicorn $(APP) --host $(HOST) --port $(PORT)

dev: ## 本地启动服务并开启热重载
	uv run uvicorn $(APP) --host $(HOST) --port $(PORT) --reload

test: ## 运行测试（可加 ARGS="-k name"）
	uv run pytest $(ARGS)

docker-up: ## 构建并后台启动容器
	docker compose up -d --build

docker-down: ## 停止并移除容器
	docker compose down

docker-logs: ## 跟踪 publish 容器日志
	docker compose logs -f publish

clean: ## 清理本地运行时产物与缓存
	rm -rf .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
