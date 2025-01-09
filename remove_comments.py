import sys
import re


def remove_comments(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()

    # 保存所有字符串的内容
    strings = {}
    string_count = 0

    # 先替换f-strings
    f_string_pattern = r'f[\'\"].*?[\'\"]'
    for match in re.finditer(f_string_pattern, content):
        placeholder = f'__STRING_{string_count}__'
        strings[placeholder] = match.group()
        content = content.replace(match.group(), placeholder)
        string_count += 1

    # 替换普通字符串
    string_pattern = r'[\'\"].*?[\'\"]'
    for match in re.finditer(string_pattern, content):
        placeholder = f'__STRING_{string_count}__'
        strings[placeholder] = match.group()
        content = content.replace(match.group(), placeholder)
        string_count += 1

    # 移除多行注释
    content = re.sub(r'"""[\s\S]*?"""', '', content)
    content = re.sub(r"'''[\s\S]*?'''", '', content)

    # 移除单行注释
    lines = content.split('\n')
    cleaned_lines = []

    for line in lines:
        comment_pos = line.find('#')
        if comment_pos != -1:
            line = line[:comment_pos]
        cleaned_lines.append(line.rstrip())

    content = '\n'.join(cleaned_lines)

    # 还原所有字符串
    for placeholder, string in strings.items():
        content = content.replace(placeholder, string)

    # 写回文件
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(content)
    print(f"已成功处理文件: {file_path}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("使用方法: python remove_comments.py <python文件路径1> [python文件路径2 ...]")
        sys.exit(1)

    for file_path in sys.argv[1:]:
        remove_comments(file_path)
