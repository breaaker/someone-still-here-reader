# 此处仍有人

原创恐怖长篇，连载中。

## 在线阅读

📖 **<https://breaaker.github.io/someone-still-here-reader/>**

手机上直接打开即可。阅读器支持：

- 章节目录抽屉（按卷分组）
- 字号、行距、段距调节，宋体／楷体／仿宋／黑体切换
- 日间 / 夜间模式
- 自动记住上次读到的章节与位置，顶部有阅读进度条
- 键盘左右方向键翻章；网址加 `#3` 可直接跳到第三章

## 仓库结构

```text
正文/                      小说稿源：每章一个 Markdown 文件，按卷分目录
reader/template.html       阅读器外壳（样式与交互）
reader/site.json           书名、简介等站点信息
scripts/reader_build.py    构建脚本：把正文编译成 dist/index.html
dist/                      上线产物，由 GitHub Actions 发布到 GitHub Pages
tests/                     构建工具的回归测试
```

## 本地构建

```bash
python3 scripts/reader_build.py build      # 由正文重建 dist/index.html
python3 scripts/reader_build.py validate   # 校验已发布的网页与正文是否一致
python3 -m unittest discover -s tests -v   # 回归测试
```

## 版权

文字版权归作者所有。未经许可，请勿转载或用于商业用途。
