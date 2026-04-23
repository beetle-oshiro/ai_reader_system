from flask import Flask, render_template, request
from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = Flask(__name__)


def get_fallback_mode(text, current_mode):
    """
    ChatGPTの判定がおかしい時の保険。
    入力文の雰囲気から、現在の選択とは別の候補をPython側で決める。
    """
    lower_text = text.lower()

    # エラーっぽいキーワード
    error_keywords = [
        "error", "exception", "traceback", "failed", "undefined",
        "module", "not found", "syntax", "TypeError", "NameError",
        "ModuleNotFoundError", "ValueError", "500", "404"
    ]

    # 規約・契約っぽいキーワード
    rule_keywords = [
        "利用規約", "契約", "同意", "料金", "請求", "追加料金",
        "支払い", "解約", "返金", "条件", "発生", "サービス"
    ]

    # 求人っぽいキーワード
    job_keywords = [
        "求人", "月給", "時給", "年収", "募集", "勤務", "勤務地",
        "雇用", "試用期間", "待遇", "残業", "未経験歓迎", "応募"
    ]

    # 文章の解読っぽいキーワード
    text_keywords = [
        "文章", "意味", "解読", "要約", "説明", "内容"
    ]

    # スコア方式
    score_map = {
        "rule": 0,
        "error": 0,
        "text": 0,
        "job": 0
    }

    for word in error_keywords:
        if word.lower() in lower_text:
            score_map["error"] += 1

    for word in rule_keywords:
        if word in text:
            score_map["rule"] += 1

    for word in job_keywords:
        if word in text:
            score_map["job"] += 1

    for word in text_keywords:
        if word in text:
            score_map["text"] += 1

    # 現在の選択は候補から外す
    score_map.pop(current_mode, None)

    # 最大スコアのものを返す
    best_mode = max(score_map, key=score_map.get)

    # 全部0なら無難に text に寄せる
    if score_map[best_mode] == 0:
        if current_mode != "text":
            return "text"
        elif current_mode != "rule":
            return "rule"
        elif current_mode != "error":
            return "error"
        else:
            return "job"

    return best_mode


def get_ng_suggestion(text, mode, mode_desc, mode_name_map):
    """
    NG時のおすすめモードを取得する。
    1回目で同じmodeが返ってきたら、再判定。
    それでもダメならPython側で補正する。
    """
    ng_response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "user",
                "content": f"""
次の文章は「{mode_desc}」には適していません。
この文章にもっとも近い項目を、必ず現在の選択肢とは別のものから1つ選んでください。

【現在の選択】
{mode}

【選べるおすすめモードキー】
rule / error / text / job

【絶対ルール】
- 現在の選択と同じキーは絶対に返さないでください。
- 返答形式は必ず次の形だけにしてください。
おすすめモードキー|短い理由

【文章】
{text}
"""
            }
        ]
    )

    ng_result = ng_response.choices[0].message.content.strip()

    parts = ng_result.split("|", 1)

    if len(parts) == 2:
        suggested_mode_key = parts[0].strip()
        reason = parts[1].strip()

        # 同じmodeや不正キーなら再補正
        if suggested_mode_key == mode or suggested_mode_key not in mode_name_map:
            suggested_mode_key = get_fallback_mode(text, mode)

        return suggested_mode_key, reason

    # 形式が崩れた時も保険で補正
    suggested_mode_key = get_fallback_mode(text, mode)
    reason = "入力内容の傾向から、こちらの項目の方が近いと判断しました。"
    return suggested_mode_key, reason


@app.route("/", methods=["GET", "POST"])
def index():
    result = ""
    text = ""
    mode = ""
    action = ""
    is_valid_mode = False

    mode_name_map = {
        "rule": "規約・契約の解説",
        "error": "エラーの解説",
        "text": "文章の解読",
        "job": "求人票の読み解き"
    }

    if request.method == "POST":
        text = request.form.get("text", "")
        mode = request.form.get("mode", "")
        action = request.form.get("action", "analyze")

        mode_desc = mode_name_map.get(mode, "")

        try:
            # 入力不足チェック
            if not text.strip():
                result = "文章が入力されていません。"
                return render_template("index.html", result=result, text=text, mode=mode)

            if not mode:
                result = "項目が選択されていません。"
                return render_template("index.html", result=result, text=text, mode=mode)

            # ① まず、内容チェック
            check_response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {
                        "role": "user",
                        "content": f"""
次の文章と選択項目の相性を判定してください。

【選択項目】
{mode_desc}

【返答ルール】
次の3パターンのどれか1つで返してください。

1. 内容が選択項目に合っている場合
OK

2. 内容は選択項目に大きく外れていないが、情報不足で一般的な説明しかできない場合
LACK|短い理由

3. 内容が選択項目に合っていない場合
NG|おすすめモードキー|短い理由

【おすすめモードキー】
rule / error / text / job

【注意】
- 今選ばれている項目と同じキーをおすすめしないでください。
- 返答は必ず上の形式だけにしてください。

【文章】
{text}
"""
                    }
                ]
            )

            check_result = check_response.choices[0].message.content.strip()

            # ② OK または LACK のときは通常解説へ進む
            if check_result == "OK" or check_result.startswith("LACK|"):
                is_valid_mode = True
                lack_message = ""

                if check_result.startswith("LACK|"):
                    parts = check_result.split("|", 1)
                    if len(parts) == 2:
                        lack_message = (
                            "※ この項目で問題ありませんが、入力内容が少ないため一般的な説明になります。"
                            f"（{parts[1]}）"
                        )

                if action == "easy":
                    prompt = f"""
次の内容を、中学生でもわかるくらい簡単に説明してください。
難しい言葉はできるだけ使わず、やさしく短めに説明してください。

項目:
{mode_desc}

文章:
{text}
"""
                else:
                    if mode == "rule":
                        prompt = f"""
次の利用規約や契約文を、初心者にもわかるように説明してください。

以下の形式で、必ず改行して書いてください：

【どういう意味か】
〜〜〜

【注意点】
〜〜〜

{text}
"""
                    elif mode == "error":
                        prompt = f"""
次のエラー文の原因と解決方法を、初心者にもわかるように説明してください。

{text}
"""
                    elif mode == "text":
                        prompt = f"""
次の文章をわかりやすく解説してください。

{text}
"""
                    elif mode == "job":
                        prompt = f"""
次の求人票の内容をわかりやすく解説し、注意点があれば教えてください。

{text}
"""
                    else:
                        prompt = "入力が不正です。"

                response = client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )

                result = response.choices[0].message.content

                if lack_message:
                    result = lack_message + "\n\n" + result

            # ③ NG のときは別モードを提案
            elif check_result.startswith("NG|"):
                parts = check_result.split("|", 2)

                if len(parts) >= 3:
                    suggested_mode_key = parts[1].strip()
                    reason = parts[2].strip()

                    # 同じmodeが返ってきたら再取得
                    if suggested_mode_key == mode or suggested_mode_key not in mode_name_map:
                        suggested_mode_key, reason = get_ng_suggestion(
                            text=text,
                            mode=mode,
                            mode_desc=mode_desc,
                            mode_name_map=mode_name_map
                        )

                    suggested_mode_name = mode_name_map.get(suggested_mode_key, "別の項目")

                    result = (
                        f"この内容は「{suggested_mode_name}」として解釈するのがおすすめです。\n"
                        f"（現在の選択: {mode_desc}）\n"
                        f"→ {reason}"
                    )
                else:
                    # NG形式が壊れていた時の保険
                    suggested_mode_key, reason = get_ng_suggestion(
                        text=text,
                        mode=mode,
                        mode_desc=mode_desc,
                        mode_name_map=mode_name_map
                    )

                    suggested_mode_name = mode_name_map.get(suggested_mode_key, "別の項目")

                    result = (
                        f"この内容は「{suggested_mode_name}」として解釈するのがおすすめです。\n"
                        f"（現在の選択: {mode_desc}）\n"
                        f"→ {reason}"
                    )

            else:
                result = "判定結果を正しく取得できませんでした。もう一度試してください。"

        except Exception as e:
            result = f"エラー: {e}"

    return render_template(
        "index.html",
        result=result,
        text=text,
        mode=mode,
        is_valid_mode=is_valid_mode
    )


if __name__ == "__main__":
    app.run(debug=True)