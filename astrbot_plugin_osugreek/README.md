# astrbot-plugin-osugreek
<h1 align="center">✨ 4k希腊字母BG生成器 ✨</h1>
<p align="center">
✨ 在图片上添加osu!mania 4k神秘希腊字母的 NoneBot2 插件，可批量生产练习图BG ✨  为[nonebot-plugin-osugreek](https://github.com/ElainaFanBoy/nonebot-plugin-osugreek)的移植版本，并进行一些优化。
</p>
<p align="center">
  <a href="https://raw.githubusercontent.com/cscs181/QQ-Github-Bot/master/LICENSE">
    <img src="https://img.shields.io/github/license/cscs181/QQ-Github-Bot.svg" alt="license">
  </a>
  <a href="https://pypi.python.org/pypi/nonebot-plugin-analysis-bilibili">
    <img src="https://img.shields.io/pypi/v/nonebot-plugin-analysis-bilibili.svg" alt="pypi">
  </a>
  <img src="https://img.shields.io/badge/python-3.8+-blue.svg" alt="python">
</p>


## 介绍

- 在图片中央贴上神秘的4k希腊字母
- 顺便添加色散视觉效果
- 添加对大小写不敏感、希腊字母本身的识别支持


## 使用

### 基础命令

```shell
/osugreek <希腊字母名称> [色散强度] [故障强度] # 未填则使用默认强度
```
或
```shell
/希腊字母 <希腊字母名称> [色散强度] [故障强度] # 未填则使用默认强度
```

### 使用方式
- 回复图片消息并输入：/osugreek <希腊字母名称> \[色散强度\] \[故障强度\]

<details> 
<summary><strong>示例</strong></summary>

![](https://i.ibb.co/xTL64vr/228922e3afd8a362ad5612a0645951b7.jpg)
*我得了一种看见希腊字母就会笑的病*
</details>


### 其他


<details><summary><strong>配置</strong></summary>


在 `.env` 文件中可以设置以下配置项：

```env
# RGB分离强度 (范围1-20, 默认4)
osugreek_chromatic_intensity: int = 4
# 故障效果强度 (范围0-5, 默认0, 0表示无故障效果)
osugreek_glitch_intensity: int = 0
```
</details>
<details><summary><strong>图片</strong></summary>

  
- 默认提取 **images/** 目录内的所有PNG格式文件
- 如果需要添加或修改新的希腊字母，只需将 PNG 图片放入 images/ 目录即可

</details>
