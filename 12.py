"""
STEP Coach — Pearl Reef (Full Stable Edition)
Run: pip install flask edge-tts gtts && python app.py

للتفعيل الذكاء الاصطناعي (OpenRouter):
    ضع مفتاح OpenRouter في OPENROUTER_API_KEY أدناه، أو اضبط متغير البيئة:
    Windows:  set OPENROUTER_API_KEY=xxx
    Mac/Linux: export OPENROUTER_API_KEY=xxx
"""
import io, os, random, sqlite3, json, time, threading, asyncio, tempfile, re, subprocess, hashlib
import urllib.request, urllib.error
import socket
from flask import Flask, abort, jsonify, render_template_string, request, send_file

try:
    import edge_tts
    EDGE_TTS_OK = True
except ImportError:
    EDGE_TTS_OK = False

try:
    from gtts import gTTS
    GTTS_OK = True
except ImportError:
    GTTS_OK = False

app = Flask(__name__)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "step_coach.db")

TTS_DIR = os.path.join(tempfile.gettempdir(), "step_coach_tts")
os.makedirs(TTS_DIR, exist_ok=True)
TTS_FILES = {}
TTS_LOCK = threading.Lock()
TTS_MAX_AGE = 30

BLANK_PAUSE_MS = 900

# ============================================================
#  AI (OpenRouter)
# ============================================================
OPENROUTER_API_KEY = "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"


AI_SYSTEM_PROMPT = """أنت "مساعد STEP الذكي" داخل تطبيق STEP Coach لاختبار STEP السعودي.
مهمتك: تشرح للطالب بأسلوب ودود ومبسّط، وتستخدم نفس لغة التصميم الزجاجية للموقع (الكلاسات).

## قواعد الإخراج (إجبارية):
1. أخرج الإجابة كاملة وبدفعة واحدة، بالعربية.
2. لا تستخدم <div class="card"> إطلاقاً — فقاعة الدردشة نفسها هي الإطار.
3. لا تستخدم <h1> ولا <h2> ولا .btn. لا تكتب style="" إطلاقاً.
4. الصندوقان .gold و .trap: صندوق واحد من كل نوع كحد أقصى لكل رد.
5. أخرج HTML فقط بدون أي شرح خارجه. لا code fences.

## العناصر المسموحة (كلاسات الموقع):
- <div class="q">          : سؤال الطالب مُعاد صياغته باختصار (اختياري)
- <span class="k">         : عنوان صغير للقسم
- <p>                       : فقرة عادية
- <div class="en">          : نص إنجليزي (LTR تلقائي)
- <div class="formula">     : صيغة إنجليزية مختصرة
- <div class="row">         : صف جدول 3 أعمدة (كلمة/ترجمة/دور)
- <div class="row h">       : رأس الجدول
- <div class="row c2">      : صف عمودين (للمقارنة)
- <span class="chip">       : وسم صغير (كلمة إشارة)
- <div class="gold">        : القاعدة الذهبية — واحد فقط
- <div class="trap">        : فخ قياس — واحد فقط إن وُجد

## قاعدة الأعمدة (.row):
استخدم الأعمدة **فقط** عند:
- تفكيك جملة كلمة بكلمة
- مقارنة صريحة بين خيارين/صيغتين
- جدول صيغ نحوية

في كل ما عدا ذلك — فكرة، شرح، نصيحة، مفهوم — اكتب <p> عادية. لا تفرض أعمدة.

## أمثلة مرجعية:

### مثال 1 — تفكيك جملة (يحتاج أعمدة):
<div class="q">ليش since وما for؟</div>
<span class="k">تفكيك الجملة</span>
<div class="en">She has lived here since 2015.</div>
<div class="row h"><span>الكلمة</span><span>الترجمة</span><span>الدور</span></div>
<div class="row"><b>has lived</b><span>عاشت</span><span>مضارع تام</span></div>
<div class="row"><b>since</b><span>منذ</span><span>كلمة إشارة</span></div>
<div class="gold"><b>القاعدة الذهبية:</b> since + نقطة زمنية، for + مدة.</div>
<div class="trap"><b>فخ قياس:</b> since three years ❌ — الصح for three years.</div>

### مثال 2 — سؤال مفهومي (بدون أعمدة):
<div class="q">شنو النوع الثاني من الشرطية؟</div>
<span class="k">توضيح</span>
<p>النوع الثاني يعبّر عن شيء غير حقيقي في الحاضر. الفعل بعد if يأتي ماضياً، والفعل في الجواب مع would.</p>
<div class="gold"><b>القاعدة الذهبية:</b> If + past، would + base verb.</div>

### مثال 3 — مقارنة بين خيارين (عمودان):
<span class="k">الفرق</span>
<div class="row c2 h"><span>mustn't</span><span>don't have to</span></div>
<div class="row c2"><b>ممنوع</b><span>غير لازم — اختياري</span></div>
<div class="gold"><b>القاعدة الذهبية:</b> mustn't = منع، don't have to = اختيار.</div>

## انتهت الأمثلة.
تذكّر: إن احترت بين أعمدة وفقرة، اختر الفقرة.
"""


def build_ai_prompt(user_name, rule_title, card_title, card_body, card_kind, question):
    return f"""## سياق الكارد الحالي
**اسم الطالب:** {user_name or 'الطالب'}
**القاعدة:** {rule_title or 'غير محدد'}
**نوع الكارد:** {card_kind or 'غير محدد'}
**عنوان الكارد:** {card_title or 'غير محدد'}

**محتوى الكارد:**
\"\"\"
{card_body or '(لا يوجد محتوى نصي)'}
\"\"\"

## سؤال الطالب
{question}

أجب فقط عن هذا الكارد بصيغة HTML بالعناصر المسموحة (بدون .card)."""


def _has_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=3, check=True)
        return True
    except Exception:
        return False

FFMPEG_AVAILABLE = _has_ffmpeg()


VOICES = {
    ("male",   "narrator"):     "en-GB-RyanNeural",
    ("female", "narrator"):     "en-GB-SoniaNeural",
    ("male",   "young"):        "en-US-EricNeural",
    ("female", "young"):        "en-US-MichelleNeural",
    ("male",   "professional"): "en-US-GuyNeural",
    ("female", "professional"): "en-US-AriaNeural",
    ("male",   "host"):         "en-US-GuyNeural",
    ("female", "host"):         "en-US-JennyNeural",
    ("male",   "default"):      "en-US-GuyNeural",
    ("female", "default"):      "en-US-JennyNeural",
}


def pick_voice(description):
    d = (description or "").lower()
    if "female" in d or "woman" in d or "girl" in d or "she" in d:
        gender = "female"
    elif "male" in d or "man" in d or "boy" in d or "he" in d:
        gender = "male"
    else:
        gender = "male"
    if "narrator" in d:
        role = "narrator"
    elif "young" in d or "teen" in d or "applicant" in d:
        role = "young"
    elif "professional" in d or "manager" in d:
        role = "professional"
    elif "host" in d or "teacher" in d:
        role = "host"
    else:
        role = "default"
    return VOICES.get((gender, role), VOICES.get((gender, "default"), "en-US-GuyNeural"))


def clean_tts_text(text):
    if not text:
        return ""
    text = re.sub(r'https?://\S+', ' ', text)
    text = re.sub(r'www\.\S+', ' ', text)
    text = re.sub(r'\b[\w\.-]+@[\w\.-]+\.\w+\b', ' ', text)
    text = re.sub(r'\b\w+\.(com|net|org|sa|gov|edu|io|co|info)(/\S*)?\b',
                  ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\[[^\]]*\]', ' ', text)
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'`+', '', text)
    text = re.sub(r'~+', '', text)
    text = re.sub(r'#+', '', text)
    text = re.sub(r'[/\\@\$%\^&+=|<>\{\}]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def split_at_blanks(text):
    if not text:
        return []
    pattern = r'_{3,}|-{3,}|\.{4,}'
    parts = re.split(pattern, text)
    return [p.strip() for p in parts]


RULE_COLORS = [
    dict(c1="#4facfe", c2="#2563eb", cs="#e6f0ff", ci="#1e3a8a", cg="rgba(37,99,235,.45)"),
    dict(c1="#8b7bff", c2="#5b3df0", cs="#ecebff", ci="#3b2aa8", cg="rgba(91,61,240,.45)"),
    dict(c1="#c084fc", c2="#8b34e8", cs="#f3e8ff", ci="#5b21b6", cg="rgba(139,52,232,.45)"),
    dict(c1="#e879f9", c2="#c026d3", cs="#fbe8ff", ci="#86198f", cg="rgba(192,38,211,.45)"),
    dict(c1="#2dd4bf", c2="#0d9488", cs="#dcf7f3", ci="#0f5f58", cg="rgba(13,148,136,.45)"),
    dict(c1="#38bdf8", c2="#0284c7", cs="#e0f4ff", ci="#075985", cg="rgba(2,132,199,.45)"),
    dict(c1="#fbbf24", c2="#f59e0b", cs="#fff3cf", ci="#7c4a03", cg="rgba(245,158,11,.45)"),
    dict(c1="#fb923c", c2="#ea580c", cs="#ffead9", ci="#8a3a0a", cg="rgba(234,88,12,.45)"),
    dict(c1="#e0b184", c2="#b0743e", cs="#f7ecdf", ci="#6b4120", cg="rgba(176,116,62,.45)"),
    dict(c1="#8da2d6", c2="#44588f", cs="#e8ecf8", ci="#26346a", cg="rgba(68,88,143,.45)"),
    dict(c1="#a78bfa", c2="#6d28d9", cs="#ede9fe", ci="#4c1d95", cg="rgba(109,40,217,.45)"),
    dict(c1="#f472b6", c2="#be185d", cs="#fce7f3", ci="#831843", cg="rgba(190,24,93,.45)"),
]


# ============================================================
#  RULES
# ============================================================
RULES = [
dict(key="sva", title="تطابق الفاعل والفعل", subtitle="Subject–Verb Agreement",
     icon="⚖️", theme=0, short="الفعل يتبع الفاعل — مفرد مع مفرد، جمع مع جمع.",
     steps=[
       dict(kind="idea", title="أول شيء نتعلمه: شنو الفاعل؟",
            body="""قبل ما نفهم أي شيء عن القواعد، لازم نعرف مين هو (الفاعل).

الفاعل هو الشيء أو الشخص الذي يقوم بالفعل في الجملة.

في المشهد: علي يأكل. الفعل يأكل، والذي يقوم به علي. علي هو الفاعل.

الترجمة: Ali eats.

الإسم (Ali) في البداية لأنه الفاعل، والفعل (eats) بعده.

كل جملة إنجليزية: الفاعل ← ثم ← الفعل ← ثم ← بقية الجملة.""",
            reveal="طيب، ليش الفعل يتغير شكله في بعض الجمل؟"),
       dict(kind="idea", title="الحين لغز (s): ليش الفعل أحياناً ينتهي بـ s؟",
            body="""    Ali eats rice.          (علي يأكل رزاً)
    Ali and Omar eat rice.  (علي وعمر يأكلان رزاً)

- الأولى: (eats) — فيها s.
- الثانية: (eat) — بدون s.

السبب: الفاعل تغيّر. مفرد → s. جمع → بدون s.

تنبيه: القاعدة تنطبق على المضارع البسيط فقط.""",
            reveal="الحين خلنا نشوف هذي القاعدة على شكل جدول واضح."),
       dict(kind="word_analysis", title="الجملة الأولى: (Ali eats rice) مفككة",
            sentence="Ali eats rice.",
            translation_ar="علي يأكل الأرز.",
            words=[
              dict(en="Ali",   ar="علي",  role="subject", role_ar="فاعل",  note="الشخص الذي يقوم بالفعل"),
              dict(en="eats",  ar="يأكل", role="verb",    role_ar="فعل",   note="الفعل — فيه s لأنه فاعله مفرد"),
              dict(en="rice",  ar="أرزاً", role="object", role_ar="مفعول", note="الشيء الذي وقع عليه الأكل"),
            ],
            lesson="كل كلمة جبت لك معناها العربي بالضبط تحت مكانها.",
            reveal="الحين خلنا نشوف الجملة الثانية ونقارن."),
       dict(kind="word_analysis", title="الجملة الثانية: (Ali and Omar eat rice) مفككة",
            sentence="Ali and Omar eat rice.",
            translation_ar="علي وعمر يأكلان الأرز.",
            words=[
              dict(en="Ali",   ar="علي",  role="subject", role_ar="فاعل",  note="الفاعل الأول"),
              dict(en="and",   ar="و",    role="conj",    role_ar="أداة ربط", note="تربط الفاعلين معاً"),
              dict(en="Omar",  ar="عمر",  role="subject", role_ar="فاعل",  note="الفاعل الثاني"),
              dict(en="eat",   ar="يأكلان", role="verb",  role_ar="فعل",   note="بدون s! لأن الفاعل جمع"),
              dict(en="rice",  ar="أرزاً", role="object", role_ar="مفعول", note="نفس المفعول"),
            ],
            lesson="لما صار الفاعل جمع (Ali AND Omar)، الفعل فقد الـs.",
            reveal="الحين خلنا نصنع جدول واضح للصيغة."),
       dict(kind="formula", title="الصيغة في جدول واضح",
            rows=[
              ("فاعل مفرد", "الفعل + حرف s", "The teacher writes. — المعلم يكتب."),
              ("فاعل جمع", "الفعل بدون s", "The teachers write. — المعلمون يكتبون."),
              ("I / You", "بدون s دائماً", "I write / You write."),
              ("He / She / It", "مع s دائماً", "He writes / She writes."),
            ],
            note="""تفصيل مهم:
• إذا الفعل ينتهي بـ (s, sh, ch, x, o) نضيف es.
    watch → watches / go → goes / fix → fixes
• إذا الفعل ينتهي بحرف ساكن + y، نقلب الـy إلى ies.
    study → studies / carry → carries
• إذا الفعل ينتهي بحرف متحرك + y، نضيف s فقط.
    play → plays / enjoy → enjoys""",
            reveal="طيب، والاستثناءات اللي تكلمنا عنها؟"),
       dict(kind="idea", title="الفخ الكبير الأول: الكلمات المخادعة بين الفاعل والفعل",
            body="""قياس يحط بين الفاعل والفعل كلمات كثيرة، عشان تنسى مين الفاعل الحقيقي.

    The box of apples ___ on the table.

التفكير الخاطئ: أقرب كلمة (apples) جمع → are.
التفكير الصحيح: الفاعل (box) مفرد → is.

القاعدة: بين الفاعل والفعل، أي كلمة تبدأ بـ (of, in, on, with) وصفية — تجاوزها.""",
            reveal="الحين خلنا نشوف المثال على شكل كلمات ملونة."),
       dict(kind="word_analysis", title="مثال الفخ: (The box of apples is on the table)",
            sentence="The box of apples is on the table.",
            translation_ar="صندوق التفاح على الطاولة.",
            words=[
              dict(en="The",    ar="الـ",      role="article", role_ar="أداة تعريف", note="تحدد شيئاً معروفاً"),
              dict(en="box",    ar="صندوق",     role="subject", role_ar="فاعل",       note="★ الفاعل الحقيقي — مفرد"),
              dict(en="of",     ar="من",        role="prep",    role_ar="حرف جر",      note="يبدأ عبارة وصفية"),
              dict(en="apples", ar="تفاح",      role="noun",    role_ar="اسم",         note="يوصف الصندوق فقط"),
              dict(en="is",     ar="يكون",      role="verb",    role_ar="فعل",         note="★ مفرد — طابق الفاعل"),
              dict(en="on",     ar="على",       role="prep",    role_ar="حرف جر",      note="للمكان"),
              dict(en="the",    ar="الـ",       role="article", role_ar="أداة تعريف",  note="تحدد الطاولة"),
              dict(en="table",  ar="طاولة",     role="noun",    role_ar="اسم",         note="نهاية الجملة"),
            ],
            lesson="تجاوز (of apples) لأنها وصفية، وارجع للفاعل (box) → is.",
            reveal="مثال ثاني."),
       dict(kind="word_analysis", title="مثال آخر: (Each of the students has a laptop)",
            sentence="Each of the students has a laptop.",
            translation_ar="كل واحد من الطلاب عنده لابتوب.",
            words=[
              dict(en="Each",     ar="كل واحد", role="subject", role_ar="فاعل",  note="★ مفرد دائماً"),
              dict(en="of",       ar="من",      role="prep",    role_ar="حرف جر", note="يبدأ عبارة وصفية"),
              dict(en="the",      ar="الـ",     role="article", role_ar="أداة",    note=""),
              dict(en="students", ar="طلاب",    role="noun",    role_ar="اسم",     note="يوصف (each) فقط"),
              dict(en="has",      ar="يملك",    role="verb",    role_ar="فعل",     note="★ مفرد"),
              dict(en="a",        ar="واحد",    role="article", role_ar="أداة",    note="للمفرد"),
              dict(en="laptop",   ar="لابتوب",  role="object",  role_ar="مفعول",   note="الشيء المملوك"),
            ],
            lesson="(each, every, either, neither, everyone, someone) تُعامل كمفرد.",
            reveal="الحين خلنا نشوف فخ neither…nor."),
       dict(kind="word_analysis", title="فخ خطير: (Neither…nor)",
            sentence="Neither the manager nor the workers are ready.",
            translation_ar="لا المدير ولا العمال جاهزون.",
            words=[
              dict(en="Neither",  ar="لا",       role="conj",    role_ar="أداة ربط", note="بداية (neither…nor)"),
              dict(en="the",      ar="الـ",       role="article", role_ar="أداة",    note=""),
              dict(en="manager",  ar="المدير",    role="noun",    role_ar="اسم",     note="الفاعل الأول"),
              dict(en="nor",      ar="ولا",       role="conj",    role_ar="أداة ربط", note="★ شريكة (neither)"),
              dict(en="the",      ar="الـ",       role="article", role_ar="أداة",    note=""),
              dict(en="workers",  ar="العمال",    role="subject", role_ar="فاعل",    note="★ الأقرب للفعل"),
              dict(en="are",      ar="يكونون",    role="verb",    role_ar="فعل",     note="★ جمع"),
              dict(en="ready",    ar="جاهزون",    role="adj",     role_ar="صفة",     note="حالة"),
            ],
            lesson="في neither…nor الفعل يتبع الفاعل الأقرب له.",
            reveal="الحين نجمع كل كلمات الإشارة."),
       dict(kind="signals", title="كلمات الإشارة — تسرّع الحل",
            items=[
              ("each / every / one of", "يعني: فعل مفرد دائماً"),
              ("a number of", "يعني: فعل جمع (مجموعة من)"),
              ("the number of", "يعني: فعل مفرد (العدد)"),
              ("of / in / on / with", "يعني: عبارات وصفية — تجاوزها"),
              ("either…or / neither…nor", "يعني: الفعل يتبع الأقرب"),
              ("either / neither (وحدهما)", "يعني: مفرد دائماً"),
            ],
            reveal="الحين الفخاخ في قياس."),
       dict(kind="traps", title="فخاخ قياس المتكررة",
            items=[
              ("The box of apples ___ on the table.", "الفاعل box مفرد → is."),
              ("The list of items ___ long.", "list مفرد → is."),
              ("Neither the manager nor the workers ___.", "workers الأقرب → are."),
              ("Either of the two options ___ fine.", "either → مفرد → is."),
              ("Each of them ___ happy.", "each → مفرد → is."),
              ("A number of students ___ absent.", "a number of → جمع → are."),
            ],
            reveal="الحين الخلاصة."),
       dict(kind="summary", title="خلاصة القاعدة في نقاط",
            points=[
              "الفاعل = اللي يسوي الفعل، وليس أقرب اسم للفعل.",
              "فاعل مفرد → الفعل + s. فاعل جمع → الفعل بدون s.",
              "العبارات بين الفاعل والفعل وصفية — تجاوزها.",
              "each/every/either/neither/one of → مفرد دائماً.",
              "a number of → جمع. the number of → مفرد.",
              "في either…or / neither…nor الفعل يتبع الفاعل الأقرب.",
            ]),
     ]),

dict(key="tense", title="الأزمنة", subtitle="Verb Tenses",
     icon="⏰", theme=1, short="كلمة الزمن في الجملة هي المفتاح.",
     steps=[
       dict(kind="idea", title="ليش نتعلم الأزمنة؟",
            body="""الزمن في اللغة الإنجليزية: الفعل يتغير شكله حسب الوقت.

- بالأمس أكلت. الآن آكل. غداً سآكل.
- في العربية: أكل، يأكل، سآكل.
- في الإنجليزية: طرق مختلفة — حرف، فعل مساعد، تصريف كامل.

قياس يختبر قدرتك على اختيار الزمن الصح بناء على كلمة الإشارة.

- (yesterday) → ماضي بسيط.
- (since) → مضارع تام.""",
            reveal="طيب، خلنا نبني خريطة الأزمنة من الصفر."),
       dict(kind="idea", title="خريطة الأزمنة الأربعة الأساسية",
            body="""1) المضارع البسيط — للعادات: She works every day.
2) الماضي البسيط — لحدث انتهى: She worked yesterday.
3) المضارع التام — بدأ في الماضي ومستمر: She has worked here since 2020.
4) الماضي التام — الأسبق بين حدثين: She had worked before he came.

هذي الأربعة، بفهمك لها، تجيب 80% من أسئلة الزمن.""",
            reveal="الحين خلنا نبدأ بأهم فرق: since vs for."),
       dict(kind="word_analysis", title="المضارع التام: (She has lived in Riyadh since 2015)",
            sentence="She has lived in Riyadh since 2015.",
            translation_ar="هي تعيش في الرياض من سنة 2015 إلى الآن.",
            words=[
              dict(en="She",    ar="هي",     role="subject", role_ar="فاعل",       note="فاعل مفرد"),
              dict(en="has",    ar="لديها",  role="aux",     role_ar="فعل مساعد",   note="★ علامة المضارع التام"),
              dict(en="lived",  ar="عاشت",   role="verb",    role_ar="فعل",         note="★ V3 من live"),
              dict(en="in",     ar="في",     role="prep",    role_ar="حرف جر",      note="للمكان"),
              dict(en="Riyadh", ar="الرياض", role="noun",    role_ar="اسم",         note="اسم المدينة"),
              dict(en="since",  ar="منذ",    role="signal",  role_ar="كلمة إشارة",  note="★ نقطة زمنية → مضارع تام"),
              dict(en="2015",   ar="٢٠١٥",   role="time",    role_ar="سنة",         note="نقطة البداية"),
            ],
            lesson="(since 2015) كلمة إشارة قوية على المضارع التام.",
            reveal="الحين خلنا نشوف الماضي التام."),
       dict(kind="word_analysis", title="الماضي التام: (I had finished before the teacher arrived)",
            sentence="I had finished before the teacher arrived.",
            translation_ar="كنت قد أنهيت قبل أن يصل المعلم.",
            words=[
              dict(en="I",       ar="أنا",      role="subject", role_ar="فاعل",      note=""),
              dict(en="had",     ar="كنت قد",   role="aux",     role_ar="فعل مساعد",  note="★ علامة الماضي التام"),
              dict(en="finished", ar="أنهيت",    role="verb",    role_ar="فعل",        note="★ V3 من finish"),
              dict(en="before",  ar="قبل أن",   role="conj",    role_ar="أداة ربط",   note="★ تربط الحدثين"),
              dict(en="the",     ar="الـ",      role="article", role_ar="أداة تعريف", note=""),
              dict(en="teacher", ar="المعلم",   role="noun",    role_ar="اسم",        note=""),
              dict(en="arrived", ar="وصل",      role="verb",    role_ar="فعل",        note="★ الحدث الأحدث"),
            ],
            lesson="حدثان في الماضي: الأسبق ياخذ (had + V3)، والأحدث ماضي بسيط.",
            reveal="الحين خلنا نشوف الماضي المستمر."),
       dict(kind="word_analysis", title="الماضي المستمر: (They were watching TV when I called)",
            sentence="They were watching TV when I called.",
            translation_ar="كانوا يشاهدون التلفاز عندما اتصلت.",
            words=[
              dict(en="They",     ar="هم",       role="subject", role_ar="فاعل",      note="جمع"),
              dict(en="were",     ar="كانوا",    role="aux",     role_ar="فعل مساعد",  note="★ were + V-ing"),
              dict(en="watching", ar="يشاهدون",  role="verb",    role_ar="فعل",        note="★ V-ing"),
              dict(en="TV",       ar="تلفاز",    role="object",  role_ar="مفعول",      note=""),
              dict(en="when",     ar="عندما",    role="conj",    role_ar="أداة ربط",   note="★ تربط الحدثين"),
              dict(en="I",        ar="أنا",      role="subject", role_ar="فاعل",       note=""),
              dict(en="called",   ar="اتصلت",    role="verb",    role_ar="فعل",        note="★ الحدث القاطع"),
            ],
            lesson="حدث طويل مستمر يقطعه حدث قصير: الأول was/were + V-ing، الثاني ماضي بسيط.",
            reveal="الحين كلمات الإشارة."),
       dict(kind="signals", title="كلمات الإشارة (احفظها)",
            items=[
              ("every day / always / usually / often", "مضارع بسيط"),
              ("yesterday / ago / last week / in 2010", "ماضي بسيط"),
              ("since / for / already / yet / ever / just", "مضارع تام"),
              ("before + past event / after + past event", "ماضي تام للحدث الأسبق"),
              ("while / when + was/were doing", "ماضي مستمر"),
              ("tomorrow / next week / in the future", "مستقبل"),
            ],
            reveal="الحين فخاخ قياس."),
       dict(kind="traps", title="فخاخ قياس في الأزمنة",
            items=[
              ("I have seen him yesterday. ❌", "الصح: I saw him yesterday."),
              ("since three years ❌", "الصح: for three years."),
              ("I have finished when she came. ❌", "الصح: I had finished."),
              ("He is knowing the answer. ❌", "know/understand لا تُستخدم مع -ing."),
            ],
            reveal="الحين الخلاصة."),
       dict(kind="summary", title="خلاصة الأزمنة",
            points=[
              "ابحث عن كلمة الزمن أولاً — هي الدليل.",
              "since / for → مضارع تام (has/have + V3).",
              "before + past → الحدث الأسبق ياخذ had + V3.",
              "كان يفعل شيئاً → was/were + V-ing.",
              "المضارع التام لا يُستخدم مع وقت منتهي.",
            ]),
     ]),

dict(key="passive", title="المبني للمجهول", subtitle="Passive Voice",
     icon="🔄", theme=2, short="نستخدمه عندما يكون الحدث أهم من الفاعل.",
     steps=[
       dict(kind="idea", title="شنو يعني (مبني للمجهول)؟",
            body="""قارن:

    Ali wrote the letter.        (معلوم)
    The letter was written.      (مجهول)

في الثانية: لا نعرف من كتب، أو لا يهمنا. نتكلم عن الرسالة نفسها.

متى نستخدمه؟
1) عندما لا نعرف من قام بالفعل.
2) عندما لا يهمنا من قام بالفعل.
3) في الكتابة العلمية والرسمية.""",
            reveal="طيب، كيف نبني الجملة المجهولة؟"),
       dict(kind="formula", title="صيغة المبني للمجهول",
            rows=[
              ("مضارع مجهول", "is / are + V3", "The car is washed."),
              ("ماضي مجهول", "was / were + V3", "The car was washed."),
              ("مضارع تام مجهول", "has / have been + V3", "The car has been washed."),
              ("ماضي تام مجهول", "had been + V3", "The car had been washed."),
              ("مستقبل مجهول", "will be + V3", "The car will be washed."),
              ("مع modal", "modal + be + V3", "The car must be washed."),
            ],
            note="""القاعدة الذهبية:

    فعل be بالزمن المناسب + V3.

- Ali wrote the letter → written + was → The letter was written.""",
            reveal="الحين نمشي على أمثلة حقيقية."),
       dict(kind="word_analysis", title="مثال: (The report was written by the manager)",
            sentence="The report was written by the manager.",
            translation_ar="التقرير كُتب بواسطة المدير.",
            words=[
              dict(en="The",     ar="الـ",        role="article", role_ar="أداة تعريف", note=""),
              dict(en="report",  ar="التقرير",    role="subject", role_ar="المفعول الأصلي", note="الشيء الذي وقع عليه الفعل"),
              dict(en="was",     ar="كان",        role="aux",     role_ar="فعل be",       note="★ ماضي + مفرد"),
              dict(en="written", ar="مكتوب",      role="verb",    role_ar="فعل V3",       note="★ التصريف الثالث من write"),
              dict(en="by",      ar="بواسطة",     role="prep",    role_ar="حرف جر",       note="★ يقدّم الفاعل الحقيقي"),
              dict(en="the",     ar="الـ",        role="article", role_ar="أداة تعريف",   note=""),
              dict(en="manager", ar="المدير",     role="noun",    role_ar="اسم",          note="الفاعل الحقيقي (اختياري)"),
            ],
            lesson="التركيب: المفعول + was/were + V3 + (by + الفاعل).",
            reveal="مثال آخر: مضارع مجهول."),
       dict(kind="word_analysis", title="مضارع مجهول: (English is spoken in many countries)",
            sentence="English is spoken in many countries.",
            translation_ar="الإنجليزية تُتحدَّث في دول كثيرة.",
            words=[
              dict(en="English",   ar="الإنجليزية", role="subject", role_ar="المفعول الأصلي", note="اللغة"),
              dict(en="is",        ar="يكون",      role="aux",     role_ar="فعل be",       note="★ مضارع + مفرد"),
              dict(en="spoken",    ar="متحدَّث",    role="verb",    role_ar="فعل V3",       note="★ V3 من speak"),
              dict(en="in",        ar="في",        role="prep",    role_ar="حرف جر",       note=""),
              dict(en="many",      ar="كثير من",   role="adj",     role_ar="صفة كمية",     note=""),
              dict(en="countries", ar="دول",       role="noun",    role_ar="اسم",          note="جمع"),
            ],
            lesson="لا يوجد (by + agent) — نتكلم عن الإنجليزية بشكل عام.",
            reveal="مثال ثالث: ماضي تام مجهول."),
       dict(kind="word_analysis", title="ماضي تام مجهول: (The window had been broken before we arrived)",
            sentence="The window had been broken before we arrived.",
            translation_ar="كانت النافذة قد كُسرت قبل أن نصل.",
            words=[
              dict(en="The",     ar="الـ",       role="article", role_ar="أداة",     note=""),
              dict(en="window",  ar="النافذة",    role="subject", role_ar="المفعول",  note=""),
              dict(en="had",     ar="كان قد",    role="aux",     role_ar="فعل مساعد", note="★ علامة الماضي التام"),
              dict(en="been",    ar="كان",       role="verb",    role_ar="V3 من be", note="★ بعد had يجي been"),
              dict(en="broken",  ar="مكسور",     role="verb",    role_ar="فعل V3",   note="★ V3 من break"),
              dict(en="before",  ar="قبل أن",     role="conj",    role_ar="أداة ربط",  note=""),
              dict(en="we",      ar="نحن",       role="subject", role_ar="فاعل",     note=""),
              dict(en="arrived", ar="وصلنا",      role="verb",    role_ar="فعل",      note="ماضي بسيط — الأحدث"),
            ],
            lesson="ماضي تام مجهول: had been + V3.",
            reveal="الحين الإشارات."),
       dict(kind="signals", title="إشارات تدلك على المبني للمجهول",
            items=[
              ("by + شخص أو شيء", "غالباً مبني للمجهول"),
              ("الفعل ليس له مفعول بعده", "الفاعل مجهول"),
              ("الجملة علمية أو رسمية", "غالباً مبني للمجهول"),
              ("(It is said/reported…)", "مبني للمجهول دائماً"),
            ],
            reveal="فخاخ."),
       dict(kind="traps", title="فخاخ قياس في المبني للمجهول",
            items=[
              ("The letter was wrote. ❌", "الصح: was written."),
              ("The accident was happened. ❌", "arrive/happen لا تُحوّل للمجهول."),
              ("The book has wrote. ❌", "الصح: has written."),
              ("The car was washed by Ali. ✓", "لكن Ali washed the car. أفضل عندما نعرف الفاعل."),
            ],
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة المبني للمجهول",
            points=[
              "الصيغة: be بالزمن المناسب + V3.",
              "(by + الفاعل) اختيارية.",
              "arrive / happen / occur لا تُحوّل للمجهول.",
              "بعد has/have/had لازم V3.",
            ]),
     ]),

dict(key="cond", title="الجمل الشرطية", subtitle="Conditionals",
     icon="🔀", theme=3, short="إذا + شرط → نتيجة. زمن الشرط هو المفتاح.",
     steps=[
       dict(kind="idea", title="شنو هي الجملة الشرطية؟",
            body="""إذا (صار شيء)، (يصير شيء آخر).

    If you study, you will pass.

جزءان:
- If you study — الشرط.
- you will pass — النتيجة.

شكل الجملة يتغير حسب مدى واقعية الشرط. وفي 4 أنواع.""",
            reveal="طيب، خلنا نشوف الأنواع الأربعة."),
       dict(kind="formula", title="الأنواع الأربعة",
            rows=[
              ("النوع 0 — حقائق", "If + مضارع، مضارع", "If you heat ice, it melts."),
              ("النوع 1 — احتمال حقيقي", "If + مضارع، will + فعل", "If you study, you will pass."),
              ("النوع 2 — خيالي", "If + ماضي، would + فعل", "If I had time, I would travel."),
              ("النوع 3 — ندم", "If + had + V3، would have + V3", "If I had studied, I would have passed."),
            ],
            note="""⚠️ القاعدة الذهبية المطلقة:
لا تضع ever (will) أو (would) داخل جملة الشرط (بعد if).

خطأ: If I will see him…
صح: If I see him…

خطأ: If I would have time…
صح: If I had time…""",
            reveal="الحين الأمثلة."),
       dict(kind="word_analysis", title="النوع 1 — احتمال حقيقي",
            sentence="If you study hard, you will pass the exam.",
            translation_ar="إذا درست بجد، ستنجح في الاختبار.",
            words=[
              dict(en="If",   ar="إذا",       role="conj",    role_ar="أداة شرط",  note="بداية الشرط"),
              dict(en="you",  ar="أنت",       role="subject", role_ar="فاعل",      note=""),
              dict(en="study", ar="تدرس",     role="verb",    role_ar="فعل مضارع", note="★ مضارع بسيط"),
              dict(en="hard", ar="بجد",       role="adv",     role_ar="ظرف",       note=""),
              dict(en="you",  ar="أنت",       role="subject", role_ar="فاعل",      note="بداية الجواب"),
              dict(en="will", ar="سوف",       role="aux",     role_ar="فعل مساعد", note="★ في الجواب وليس في الشرط"),
              dict(en="pass", ar="تنجح",      role="verb",    role_ar="فعل",       note="مصدر بدون to"),
              dict(en="the",  ar="الـ",       role="article", role_ar="أداة",      note=""),
              dict(en="exam", ar="الاختبار",  role="object",  role_ar="مفعول",     note=""),
            ],
            lesson="شرط حقيقي → النوع 1. if + مضارع، الجواب will + فعل.",
            reveal="النوع 2."),
       dict(kind="word_analysis", title="النوع 2 — خيالي",
            sentence="If I had more time, I would travel.",
            translation_ar="لو كان عندي وقت أكثر، لكن ما عندي، لسافرت.",
            words=[
              dict(en="If",     ar="لو",       role="conj",    role_ar="أداة شرط",  note=""),
              dict(en="I",      ar="أنا",       role="subject", role_ar="فاعل",      note=""),
              dict(en="had",    ar="أملك",      role="verb",    role_ar="فعل ماضي",  note="★ ماضي بسيط"),
              dict(en="more",   ar="أكثر",      role="adj",     role_ar="صفة",       note=""),
              dict(en="time",   ar="وقت",       role="object",  role_ar="مفعول",     note=""),
              dict(en="I",      ar="أنا",       role="subject", role_ar="فاعل",      note=""),
              dict(en="would",  ar="سوف",       role="aux",     role_ar="فعل مساعد", note="★ في الجواب"),
              dict(en="travel", ar="أسافر",     role="verb",    role_ar="فعل",       note="مصدر بدون to"),
            ],
            lesson="النوع 2: تخيّل غير حقيقي. الشرط ماضي، الجواب would + فعل.",
            reveal="النوع 3."),
       dict(kind="word_analysis", title="النوع 3 — ندم على الماضي",
            sentence="If she had studied, she would have passed.",
            translation_ar="لو كانت درست، كانت نجحت. (لكنها ما درست)",
            words=[
              dict(en="If",     ar="لو",          role="conj",    role_ar="أداة شرط",  note=""),
              dict(en="she",    ar="هي",           role="subject", role_ar="فاعل",      note=""),
              dict(en="had",    ar="كانت قد",      role="aux",     role_ar="فعل مساعد", note="★ had + V3"),
              dict(en="studied", ar="درست",        role="verb",    role_ar="فعل V3",    note=""),
              dict(en="she",    ar="هي",           role="subject", role_ar="فاعل",      note=""),
              dict(en="would",  ar="كانت سوف",     role="aux",     role_ar="فعل مساعد", note=""),
              dict(en="have",   ar="—",            role="aux",     role_ar="فعل مساعد", note="★ جزء من would have"),
              dict(en="passed", ar="نجحت",         role="verb",    role_ar="فعل V3",    note=""),
            ],
            lesson="النوع 3: had + V3 في الشرط، would have + V3 في الجواب.",
            reveal="الإشارات."),
       dict(kind="signals", title="كلمات الإشارة",
            items=[
              ("حقيقة علمية ثابتة", "النوع 0"),
              ("احتمال حقيقي في المستقبل", "النوع 1 (will)"),
              ("If I were you", "النوع 2 (خيالي)"),
              ("had + V3 في الشرط", "النوع 3"),
            ],
            reveal="الفخاخ."),
       dict(kind="traps", title="فخاخ قياس",
            items=[
              ("If I will see him… ❌", "الصح: If I see him…"),
              ("If I would have time… ❌", "الصح: If I had time…"),
              ("If he was here… ❌ في الخيال", "الصح: If he were here."),
              ("If she would have studied… ❌", "الصح: If she had studied…"),
            ],
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة الشرطية",
            points=[
              "زمن الشرط (بعد if) يحدد النوع.",
              "will / would في الجواب، وليس في الشرط.",
              "النوع 3: had + V3 شرط، would have + V3 جواب.",
              "في النوع 2، نستخدم (were) مع كل الضمائر.",
            ]),
     ]),

dict(key="rel", title="الضمائر الموصولة", subtitle="Relative Pronouns",
     icon="🔗", theme=4, short="تربط جملتين بدل تكرار الاسم.",
     steps=[
       dict(kind="idea", title="ليش نحتاج الضمائر الموصولة؟",
            body="""طريقتان لربط جملتين:

    This is the man. The man helped me.   (مكرر)
    This is the man who helped me.        (موصول)

كلمة (who) أخذت مكان (The man) المكرر وربطت الجملتين.""",
            reveal="طيب، خلنا نشوف كل واحد ومتى نستخدمه."),
       dict(kind="formula", title="الضمائر الموصولة وأدوارها",
            rows=[
              ("who",   "يبدل شخص (فاعل)", "The man who came is my uncle."),
              ("whom",  "يبدل شخص (مفعول)", "The man whom I saw is my uncle."),
              ("which", "يبدل شيء أو حيوان", "The book which I read is good."),
              ("that",  "شخص أو شيء (بدون فاصلة)", "The book that I read is good."),
              ("whose", "ملكية (بعده اسم)", "The man whose car was stolen…"),
              ("where", "مكان", "The city where I was born…"),
              ("when",  "زمان", "The day when we met…"),
            ],
            note="""قاعدة (that):
- that لا تُستخدم بعد فاصلة.
- بعد فاصلة: which أو who.

خطأ: The book, that I read, is good.
صح: The book, which I read, is good.""",
            reveal="الحين أمثلة."),
       dict(kind="word_analysis", title="whose — الملكية",
            sentence="The man whose car was stolen called the police.",
            translation_ar="الرجل الذي سُرقت سيارته اتصل بالشرطة.",
            words=[
              dict(en="The",     ar="الـ",        role="article", role_ar="أداة تعريف", note=""),
              dict(en="man",     ar="الرجل",       role="noun",    role_ar="اسم",        note="الشخص الذي نتكلم عنه"),
              dict(en="whose",   ar="الذي",        role="rel",     role_ar="ضمير موصول", note="★ بعده اسم (car) = ملكية"),
              dict(en="car",     ar="سيارته",      role="noun",    role_ar="اسم",        note="★ بعد whose يجي اسم"),
              dict(en="was",     ar="كان",         role="aux",     role_ar="فعل be",     note=""),
              dict(en="stolen",  ar="مسروقة",      role="verb",    role_ar="فعل V3",     note="مبني للمجهول"),
              dict(en="called",  ar="اتصل",        role="verb",    role_ar="فعل",        note=""),
              dict(en="the",     ar="الـ",         role="article", role_ar="أداة",       note=""),
              dict(en="police",  ar="الشرطة",      role="noun",    role_ar="اسم",        note=""),
            ],
            lesson="القاعدة الذهبية: بعد whose يجي اسم دائماً.",
            reveal="مثال آخر: that."),
       dict(kind="word_analysis", title="that — للشيء أو الشخص بدون فاصلة",
            sentence="This is the book that I told you about.",
            translation_ar="هذا هو الكتاب الذي أخبرتك عنه.",
            words=[
              dict(en="This",   ar="هذا",      role="subject", role_ar="فاعل",       note=""),
              dict(en="is",     ar="يكون",     role="verb",    role_ar="فعل",         note=""),
              dict(en="the",    ar="الـ",      role="article", role_ar="أداة",        note=""),
              dict(en="book",   ar="الكتاب",   role="noun",    role_ar="اسم",         note="شيء"),
              dict(en="that",   ar="الذي",     role="rel",     role_ar="ضمير موصول",  note="★ للشيء (بدون فاصلة)"),
              dict(en="I",      ar="أنا",      role="subject", role_ar="فاعل",        note=""),
              dict(en="told",   ar="أخبرت",    role="verb",    role_ar="فعل",         note=""),
              dict(en="you",    ar="أنت",      role="object",  role_ar="مفعول",       note=""),
              dict(en="about",  ar="عن",       role="prep",    role_ar="حرف جر",      note=""),
            ],
            lesson="that بديل عن which و who، بشرط ألا تحتوي الجملة على فاصلة.",
            reveal="مثال: where."),
       dict(kind="word_analysis", title="where — للمكان",
            sentence="That is the city where I was born.",
            translation_ar="تلك هي المدينة التي ولدت فيها.",
            words=[
              dict(en="That",   ar="تلك",    role="subject", role_ar="فاعل",      note=""),
              dict(en="is",     ar="يكون",   role="verb",    role_ar="فعل",        note=""),
              dict(en="the",    ar="الـ",    role="article", role_ar="أداة",       note=""),
              dict(en="city",   ar="المدينة", role="noun",    role_ar="اسم مكان",   note="★ مكان"),
              dict(en="where",  ar="حيث",    role="rel",     role_ar="ضمير موصول", note="★ للمكان"),
              dict(en="I",      ar="أنا",    role="subject", role_ar="فاعل",       note=""),
              dict(en="was",    ar="كنت",    role="aux",     role_ar="فعل be",     note=""),
              dict(en="born",   ar="مولود",  role="verb",    role_ar="فعل V3",     note=""),
            ],
            lesson="متى شفت city / country / place قبل الفراغ → where.",
            reveal="الإشارات."),
       dict(kind="signals", title="كلمات الإشارة",
            items=[
              ("بعده اسم مباشرة", "whose"),
              ("اسم مكان قبل الفراغ (city, country)", "where"),
              ("اسم زمان قبل الفراغ (day, year)", "when"),
              ("بعد فاصلة", "which أو whom (ليس that)"),
              ("شخص قبل الفراغ", "who / whom / that"),
            ],
            reveal="فخاخ."),
       dict(kind="traps", title="فخاخ قياس",
            items=[
              ("The man who his car… ❌", "الصح: The man whose car…"),
              ("The city which I was born… ❌", "الصح: The city where I was born."),
              ("The book, that I read… ❌", "بعد الفاصلة: which, not that."),
              ("The person which helped me… ❌", "الصح: The person who helped me."),
            ],
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة الضمائر الموصولة",
            points=[
              "who=شخص، which=شيء، whose=ملكية، where=مكان، when=زمان.",
              "that للشخص أو الشيء، لكن بدون فاصلة.",
              "بعد whose يجي اسم دائماً.",
              "بعد الفاصلة: which أو whom، وليس that.",
            ]),
     ]),

dict(key="prep", title="حروف الجر", subtitle="Prepositions",
     icon="📍", theme=5, short="in / on / at — من الأكبر للأصغر.",
     steps=[
       dict(kind="idea", title="ليش حروف الجر صعبة؟",
            body="""حروف الجر (in، on، at، for، with) كلمات قصيرة، لكن استخداماتها محددة.

في STEP، الأكثر اختباراً: in / on / at للزمن والمكان.

الحيلة: من الأكبر إلى الأصغر.

للزمن:
- in = أكبر (شهر، سنة، فصل).
- on = وسط (يوم، تاريخ).
- at = أصغر (ساعة، لحظة).

- in March / on Monday / at 6 PM.""",
            reveal="الحين نشوف القاعدة على شكل جدول."),
       dict(kind="formula", title="القاعدة",
            rows=[
              ("in", "شهر/سنة/فصل/صباح", "in March, in 2020, in winter, in the morning"),
              ("on", "يوم/تاريخ", "on Sunday, on May 3rd, on my birthday"),
              ("at", "ساعة/نقطة محددة", "at 6 o'clock, at noon, at night"),
            ],
            note="""استثناءات:
- in the morning ← لكن at night.
- in the afternoon ← لكن at noon.
- on the weekend (أمريكي) ← at the weekend (بريطاني).""",
            reveal="أمثلة."),
       dict(kind="word_analysis", title="on — للتاريخ",
            sentence="My father died on June 22.",
            translation_ar="والدي توفي في الثاني والعشرين من يونيو.",
            words=[
              dict(en="My",     ar="الخاص بي",  role="poss",    role_ar="صفة ملكية",  note=""),
              dict(en="father", ar="والدي",     role="noun",    role_ar="اسم",        note=""),
              dict(en="died",   ar="توفي",      role="verb",    role_ar="فعل ماضي",   note=""),
              dict(en="on",     ar="في",        role="prep",    role_ar="حرف جر زمني", note="★ تاريخ → on"),
              dict(en="June",   ar="يونيو",     role="noun",    role_ar="شهر",        note=""),
              dict(en="22",     ar="٢٢",        role="num",     role_ar="عدد",        note="يوم محدد"),
            ],
            lesson="التاريخ (يوم + شهر) ياخذ on. in March لكن on March 22.",
            reveal="مثال: at."),
       dict(kind="word_analysis", title="at — للساعة",
            sentence="We will meet at 8 o'clock.",
            translation_ar="سنتقابل في الساعة الثامنة.",
            words=[
              dict(en="We",       ar="نحن",      role="subject", role_ar="فاعل",      note=""),
              dict(en="will",     ar="سوف",      role="aux",     role_ar="فعل مساعد", note=""),
              dict(en="meet",     ar="نتقابل",   role="verb",    role_ar="فعل",       note=""),
              dict(en="at",       ar="عند",      role="prep",    role_ar="حرف جر زمني", note="★ ساعة محددة → at"),
              dict(en="8",        ar="٨",        role="num",     role_ar="عدد",       note=""),
              dict(en="o'clock",  ar="بالساعة",  role="adv",     role_ar="ظرف",       note=""),
            ],
            lesson="الساعة المحددة تاخذ at دائماً.",
            reveal="مثال: in للمدن."),
       dict(kind="word_analysis", title="in — للمدن",
            sentence="My uncle lives in Jeddah.",
            translation_ar="عمي يسكن في جدة.",
            words=[
              dict(en="My",     ar="الخاص بي", role="poss",    role_ar="صفة ملكية", note=""),
              dict(en="uncle",  ar="عمي",      role="noun",    role_ar="اسم",       note=""),
              dict(en="lives",  ar="يسكن",     role="verb",    role_ar="فعل مضارع", note=""),
              dict(en="in",     ar="في",       role="prep",    role_ar="حرف جر مكاني", note="★ مدينة → in"),
              dict(en="Jeddah", ar="جدة",      role="noun",    role_ar="اسم مدينة", note=""),
            ],
            lesson="المناطق الكبيرة (مدن، دول، قارات) تاخذ in.",
            reveal="الإشارات."),
       dict(kind="signals", title="كلمات الإشارة",
            items=[
              ("month / year / season / morning / afternoon / city / country", "in"),
              ("day / date / Monday / May 3rd", "on"),
              ("clock time / night / noon / midnight", "at"),
            ],
            reveal="فخاخ."),
       dict(kind="traps", title="فخاخ قياس",
            items=[
              ("in Sunday ❌ → on Sunday", ""),
              ("on the morning ❌ → in the morning", ""),
              ("at March ❌ → in March", ""),
              ("at the morning ❌ → in the morning", ""),
            ],
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة حروف الجر",
            points=[
              "in = الكبير (شهر، سنة، مدينة).",
              "on = المتوسط (يوم، تاريخ).",
              "at = الصغير (ساعة، نقطة).",
              "in the morning / at night.",
            ]),
     ]),

dict(key="pron", title="الضمائر والملكية", subtitle="Pronouns & Possessives",
     icon="👤", theme=6, short="للضمير 5 أشكال، كل شكل له وظيفة.",
     steps=[
       dict(kind="idea", title="ليش الضمائر تتغير؟",
            body="""في العربية: أنا في كل المواقع.

في الإنجليزية: الضمير يتغير شكله حسب وظيفته.

- I went. (فاعل)
- Ahmed saw me. (مفعول)
- My book. (ملكية مع اسم)
- The book is mine. (ملكية بدون اسم)

5 أشكال. تعلمها مرة واحدة.""",
            reveal="الحين جدول الضمائر."),
       dict(kind="formula", title="جدول الضمائر الكامل",
            rows=[
              ("فاعل (Subject)", "I / he / she / it / we / they", "I saw him."),
              ("مفعول (Object)", "me / him / her / it / us / them", "He saw me."),
              ("صفة ملكية (Possessive Adj.)", "my / his / her / its / our / their", "My book."),
              ("ضمير ملكية (Possessive Pronoun)", "mine / his / hers / its / ours / theirs", "The book is mine."),
              ("انعكاسي (Reflexive)", "myself / himself / herself / itself / ourselves / themselves", "I hurt myself."),
            ],
            note="""القاعدة المهمة:
- صفة الملكية (my / your / his) → قبل اسم دائماً.
- ضمير الملكية (mine / yours / his) → بدون اسم بعده أبداً.

خطأ: mine book ❌
صح: my book ✓  أو  The book is mine ✓""",
            reveal="أمثلة."),
       dict(kind="word_analysis", title="صفة ملكية: (Sara forgot her book)",
            sentence="Sara forgot her book.",
            translation_ar="سارة نسيت كتابها.",
            words=[
              dict(en="Sara",   ar="سارة",   role="subject", role_ar="فاعل",       note=""),
              dict(en="forgot", ar="نسيت",   role="verb",    role_ar="فعل ماضي",   note=""),
              dict(en="her",    ar="ـها",    role="poss",    role_ar="صفة ملكية",  note="★ قبل الاسم (book)"),
              dict(en="book",   ar="كتابها", role="noun",    role_ar="اسم",        note="★ بعد الصفة"),
            ],
            lesson="قبل الاسم مباشرة → صفة ملكية.",
            reveal="مثال: ضمير ملكية."),
       dict(kind="word_analysis", title="ضمير ملكية: (The decision is mine, not yours)",
            sentence="The decision is mine, not yours.",
            translation_ar="القرار قراري، وليس قرارك.",
            words=[
              dict(en="The",      ar="الـ",       role="article", role_ar="أداة",           note=""),
              dict(en="decision", ar="قرار",      role="noun",    role_ar="اسم",             note=""),
              dict(en="is",       ar="يكون",      role="verb",    role_ar="فعل",             note=""),
              dict(en="mine",     ar="قراري",     role="poss",    role_ar="ضمير ملكية",      note="★ بدون اسم بعده"),
              dict(en="not",      ar="ليس",       role="neg",     role_ar="أداة نفي",        note=""),
              dict(en="yours",    ar="قرارك",     role="poss",    role_ar="ضمير ملكية",      note="★ بدون اسم بعده"),
            ],
            lesson="ضمير الملكية لا ياخذ اسم بعده أبداً.",
            reveal="مثال: انعكاسي."),
       dict(kind="word_analysis", title="انعكاسي: (He hurt himself while playing)",
            sentence="He hurt himself while playing.",
            translation_ar="أذى نفسه أثناء اللعب.",
            words=[
              dict(en="He",      ar="هو",      role="subject", role_ar="فاعل",           note=""),
              dict(en="hurt",    ar="أذى",     role="verb",    role_ar="فعل",             note=""),
              dict(en="himself", ar="نفسه",    role="refl",    role_ar="ضمير انعكاسي",    note="★ الفاعل والمفعول نفس الشخص"),
              dict(en="while",   ar="بينما",   role="conj",    role_ar="أداة ربط",        note=""),
              dict(en="playing", ar="يلعب",    role="verb",    role_ar="فعل + ing",       note=""),
            ],
            lesson="الفاعل والمفعول نفس الشخص → ضمير انعكاسي.",
            reveal="الإشارات."),
       dict(kind="signals", title="كلمات الإشارة",
            items=[
              ("قبل اسم مباشرة", "صفة ملكية (my / her / his)"),
              ("في نهاية الجملة بدون اسم", "ضمير ملكية (mine / hers)"),
              ("الفاعل والمفعول نفس الشخص", "انعكاسي (myself / himself)"),
              ("المفعول به بعد الفعل", "ضمير مفعول (me / him / her)"),
            ],
            reveal="فخاخ."),
       dict(kind="traps", title="فخاخ قياس",
            items=[
              ("its ≠ it's", "its = ملكية. it's = it is."),
              ("mine book ❌", "الصح: my book."),
              ("hisself ❌", "الصح: himself."),
              ("theirselves ❌", "الصح: themselves."),
            ],
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة الضمائر",
            points=[
              "قبل اسم → صفة ملكية (my).",
              "بدون اسم بعده → ضمير ملكية (mine).",
              "نفس الفاعل والمفعول → انعكاسي (myself).",
              "its (ملكية) تختلف عن it's (it is).",
            ]),
     ]),

dict(key="modal", title="الأفعال الناقصة", subtitle="Modals",
     icon="🔑", theme=7, short="بعدها فعل أصلي بدون to وبدون s.",
     steps=[
       dict(kind="idea", title="شنو يعني فعل ناقص؟",
            body="""الأفعال الناقصة (modals):

1) لا تتصرف: خطأ She musts. صح She must.
2) بعدها فعل أصلي بدون to: خطأ You must to go. صح You must go.
3) تعطي معنى إضافياً: إلزام، نصيحة، قدرة، احتمال.

- must (يجب).
- have to (لازم).
- should (يُفترض).
- might / could (قد).
- can (يقدر).""",
            reveal="جدول مفصل."),
       dict(kind="formula", title="جدول الأفعال الناقصة",
            rows=[
              ("must",        "إلزام قوي (من المتكلم)", "You must wear a seatbelt."),
              ("have to",     "إلزام خارجي (قانون)", "I have to work."),
              ("should",      "نصيحة", "You should rest."),
              ("might / could", "احتمال", "It might rain."),
              ("can",         "قدرة", "I can swim."),
              ("mustn't",     "ممنوع", "You mustn't smoke here."),
              ("don't have to", "غير لازم (اختياري)", "You don't have to come."),
            ],
            note="""الفرق الحرج:
- mustn't = ممنوع (forbidden).
- don't have to = غير لازم (optional).

You mustn't smoke here. (ممنوع)
You don't have to come. (مو لازم، اختياري)""",
            reveal="أمثلة."),
       dict(kind="word_analysis", title="must — إلزام",
            sentence="You must wear a seat belt; it is the law.",
            translation_ar="يجب عليك أن تلبس حزام الأمان؛ إنه القانون.",
            words=[
              dict(en="You",   ar="أنت",     role="subject", role_ar="فاعل",      note=""),
              dict(en="must",  ar="يجب",     role="modal",   role_ar="فعل ناقص",  note="★ بعدها فعل أصلي بدون to"),
              dict(en="wear",  ar="تلبس",    role="verb",    role_ar="فعل أصلي",  note="★ بدون to"),
              dict(en="a",     ar="واحد",    role="article", role_ar="أداة",      note=""),
              dict(en="seat",  ar="مقعد",    role="noun",    role_ar="اسم",       note=""),
              dict(en="belt",  ar="حزام",    role="noun",    role_ar="اسم",       note=""),
              dict(en="it",    ar="هو",      role="subject", role_ar="فاعل",      note=""),
              dict(en="is",    ar="يكون",    role="verb",    role_ar="فعل",       note=""),
              dict(en="the",   ar="الـ",     role="article", role_ar="أداة",      note=""),
              dict(en="law",   ar="القانون", role="noun",    role_ar="اسم",       note=""),
            ],
            lesson="must + فعل أصلي بدون to.",
            reveal="مثال: should."),
       dict(kind="word_analysis", title="should — نصيحة",
            sentence="You look tired. You should rest.",
            translation_ar="تبدو متعباً. ينبغي أن ترتاح.",
            words=[
              dict(en="You",    ar="أنت",     role="subject", role_ar="فاعل",      note=""),
              dict(en="look",   ar="تبدو",    role="verb",    role_ar="فعل",       note=""),
              dict(en="tired",  ar="متعباً",   role="adj",     role_ar="صفة",       note=""),
              dict(en="You",    ar="أنت",     role="subject", role_ar="فاعل",      note=""),
              dict(en="should", ar="يُفترض",  role="modal",   role_ar="فعل ناقص",  note="★ نصيحة"),
              dict(en="rest",   ar="ترتاح",   role="verb",    role_ar="فعل أصلي",  note="بدون to"),
            ],
            lesson="should + فعل أصلي — للنصيحة.",
            reveal="مثال: has to."),
       dict(kind="word_analysis", title="has to — للمفرد",
            sentence="She has to finish the report by Monday.",
            translation_ar="لازم تخلّص التقرير قبل يوم الاثنين.",
            words=[
              dict(en="She",    ar="هي",       role="subject", role_ar="فاعل",       note="مفرد"),
              dict(en="has",    ar="لديها",    role="aux",     role_ar="فعل مساعد",  note="★ للمفرد (has to)"),
              dict(en="to",     ar="أن",       role="part",    role_ar="أداة",       note="★ مع has to فقط"),
              dict(en="finish", ar="تخلّص",    role="verb",    role_ar="فعل أصلي",   note=""),
              dict(en="the",    ar="الـ",       role="article", role_ar="أداة",       note=""),
              dict(en="report", ar="التقرير",  role="object",  role_ar="مفعول",      note=""),
              dict(en="by",     ar="قبل",      role="prep",    role_ar="حرف جر",     note=""),
              dict(en="Monday", ar="الاثنين",  role="noun",    role_ar="يوم",        note=""),
            ],
            lesson="have to تتصرف: has للمفرد، have للجمع. must لا تتصرف أبداً.",
            reveal="الإشارات."),
       dict(kind="signals", title="كلمات الإشارة",
            items=[
              ("قانون / نظام / إلزام", "must / have to"),
              ("نصيحة / رأي", "should / ought to"),
              ("غير لازم (اختياري)", "don't have to"),
              ("ممنوع تماماً", "mustn't"),
              ("احتمال", "might / may / could"),
              ("قدرة", "can / could"),
            ],
            reveal="فخاخ."),
       dict(kind="traps", title="فخاخ قياس",
            items=[
              ("must to go ❌", "الصح: must go."),
              ("She musts ❌", "modals لا تتصرف."),
              ("mustn't = don't have to ❌", "خطأ كبير. mustn't = ممنوع. don't have to = غير لازم."),
              ("can to swim ❌", "الصح: can swim."),
            ],
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة الأفعال الناقصة",
            points=[
              "modal + فعل أصلي بدون to وبدون s.",
              "mustn't = ممنوع.",
              "don't have to = غير لازم.",
              "has to للمفرد، have to للجمع.",
            ]),
     ]),

dict(key="count", title="المعدود وغير المعدود", subtitle="Countable & Uncountable",
     icon="🔢", theme=8, short="many/few للمعدود، much/little لغير المعدود.",
     steps=[
       dict(kind="idea", title="الفرق الأساسي",
            body="""في الإنجليزية، التقسيم صارم:

- معدود (Countable): تقدر تعدّه. book → books.
- غير معدود (Uncountable): ما تقدر تعدّه. water, advice, information.

الفروق:
1) الجمع: معدود ياخذ s، غير معدود ما ياخذ s.
   books ✓ — waters ❌ — water ✓.
2) الكميات: many للمعدود، much لغير المعدود.""",
            reveal="جدول الكميات."),
       dict(kind="formula", title="جدول الكميات",
            rows=[
              ("many + جمع معدود", "many books", "كتب كثيرة"),
              ("much + غير معدود", "much water", "ماء كثير"),
              ("a few + جمع معدود", "a few books", "بضعة كتب (إيجابي)"),
              ("few + جمع معدود", "few books", "قليل جداً (سلبي)"),
              ("a little + غير معدود", "a little water", "قليل من الماء (إيجابي)"),
              ("little + غير معدود", "little water", "قليل جداً (سلبي)"),
            ],
            note="""الفرق:
- a few books = عندي بضعة كتب (شي موجود).
- few books = شبه ما عندي شي.

كلمات غير معدودة مشهورة:
advice, information, furniture, news, money, homework, bread.""",
            reveal="أمثلة."),
       dict(kind="word_analysis", title="much — غير معدود",
            sentence="How much water do you drink?",
            translation_ar="كم ماءاً تشرب؟",
            words=[
              dict(en="How",   ar="كم",       role="q",     role_ar="أداة استفهام", note=""),
              dict(en="much",  ar="كثير",     role="quant", role_ar="صفة كمية",    note="★ مع غير المعدود"),
              dict(en="water", ar="ماء",      role="noun",  role_ar="اسم غير معدود", note="★ ما ينعد"),
              dict(en="do",    ar="—",        role="aux",   role_ar="فعل مساعد",   note=""),
              dict(en="you",   ar="أنت",      role="subject", role_ar="فاعل",     note=""),
              dict(en="drink", ar="تشرب",     role="verb",  role_ar="فعل",         note=""),
            ],
            lesson="How much + غير معدود. How many + جمع معدود.",
            reveal="a few."),
       dict(kind="word_analysis", title="a few — معدود، إيجابي",
            sentence="There are only a few seats left.",
            translation_ar="لم يتبقَّ سوى بضعة مقاعد.",
            words=[
              dict(en="There", ar="هناك",   role="expletive", role_ar="أداة وجود", note=""),
              dict(en="are",   ar="توجد",   role="verb",      role_ar="فعل",       note=""),
              dict(en="only",  ar="فقط",    role="adv",       role_ar="ظرف",       note=""),
              dict(en="a",     ar="—",      role="article",   role_ar="أداة",      note=""),
              dict(en="few",   ar="بضعة",   role="quant",     role_ar="صفة كمية",  note="★ إيجابي مع الجمع"),
              dict(en="seats", ar="مقاعد",  role="noun",      role_ar="اسم جمع معدود", note="★ معدود"),
              dict(en="left",  ar="متبقية", role="adj",       role_ar="صفة",       note=""),
            ],
            lesson="a few = بضعة (يعني فيه شي موجود).",
            reveal="little."),
       dict(kind="word_analysis", title="little — غير معدود، سلبي",
            sentence="I have little time, so I can't join.",
            translation_ar="عندي وقت قليل جداً، لذلك لا أستطيع الانضمام.",
            words=[
              dict(en="I",     ar="أنا",         role="subject", role_ar="فاعل",          note=""),
              dict(en="have",  ar="أملك",         role="verb",    role_ar="فعل",            note=""),
              dict(en="little", ar="قليل جداً",    role="quant",   role_ar="صفة كمية",       note="★ سلبي — شبه معدوم"),
              dict(en="time",  ar="وقت",          role="noun",    role_ar="اسم غير معدود",  note=""),
              dict(en="so",    ar="لذلك",         role="conj",    role_ar="أداة ربط",       note=""),
              dict(en="I",     ar="أنا",          role="subject", role_ar="فاعل",          note=""),
              dict(en="can't", ar="لا أستطيع",    role="modal",   role_ar="فعل ناقص منفي",  note=""),
              dict(en="join",  ar="أنضم",         role="verb",    role_ar="فعل",            note=""),
            ],
            lesson="little = قليل جداً (سلبي) — شبه ما عندي.",
            reveal="الإشارات."),
       dict(kind="signals", title="كلمات الإشارة",
            items=[
              ("How many + جمع معدود", ""),
              ("How much + غير معدود", ""),
              ("a few / a little", "إيجابي (شي موجود)"),
              ("few / little", "سلبي (شبه معدوم)"),
              ("advice, information, furniture, news, money", "غير معدودة"),
            ],
            reveal="فخاخ."),
       dict(kind="traps", title="فخاخ قياس",
            items=[
              ("advices / informations / furnitures ❌", "غير معدودة، لا تاخذ s."),
              ("How many water ❌", "الصح: How much water."),
              ("a few money ❌", "الصح: a little money."),
              ("much people ❌", "الصح: many people."),
            ],
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة المعدود وغير المعدود",
            points=[
              "معدود → many / few / a few.",
              "غير معدود → much / little / a little.",
              "a few / a little = إيجابي. few / little = سلبي.",
              "advice, information, furniture, news غير معدودة.",
            ]),
     ]),

dict(key="conj", title="التوازي وأدوات الربط", subtitle="Conjunctions",
     icon="🔗", theme=9, short="كل أداة مزدوجة لها شريك ثابت.",
     steps=[
       dict(kind="idea", title="شنو التوازي؟",
            body="""لما تعطف مجموعة، لازم تكون جميعها بنفس الشكل.

خطأ: She likes swimming, reading, and to travel. (خلل)
صح: She likes swimming, reading, and traveling. (V-ing موحد)""",
            reveal="جدول أدوات الربط المزدوجة."),
       dict(kind="formula", title="أدوات الربط المزدوجة",
            rows=[
              ("both…and", "كلاهما", "both smart and hardworking"),
              ("either…or", "إما…أو", "either tea or coffee"),
              ("neither…nor", "لا…ولا", "neither Ali nor Omar"),
              ("not only…but also", "ليس فقط…بل أيضاً", "not only smart but also kind"),
              ("although / though", "رغم أن (يتبعها جملة)", "Although it rained, we went out."),
              ("despite / in spite of", "رغم (يتبعها اسم/V-ing)", "Despite the rain, we went out."),
            ],
            note="""الأخطاء الشائعة:
- both…or ❌ (الصح: both…and).
- neither…or ❌ (الصح: neither…nor).
- although the rain ❌ (الصح: despite the rain OR although it rained).""",
            reveal="أمثلة."),
       dict(kind="word_analysis", title="both…and",
            sentence="He is both smart and hardworking.",
            translation_ar="إنه ذكي ومجتهد على حد سواء.",
            words=[
              dict(en="He",          ar="هو",      role="subject", role_ar="فاعل",        note=""),
              dict(en="is",          ar="يكون",    role="verb",    role_ar="فعل",          note=""),
              dict(en="both",        ar="كلاهما",  role="conj",    role_ar="أداة ربط",     note="★ شريكتها and"),
              dict(en="smart",       ar="ذكي",     role="adj",     role_ar="صفة",          note=""),
              dict(en="and",         ar="و",       role="conj",    role_ar="أداة ربط",     note="★ شريكة both"),
              dict(en="hardworking", ar="مجتهد",   role="adj",     role_ar="صفة",          note=""),
            ],
            lesson="both دائماً مع and.",
            reveal="neither…nor."),
       dict(kind="word_analysis", title="neither…nor",
            sentence="Neither the manager nor the staff knew.",
            translation_ar="لا المدير ولا الموظفون عرفوا.",
            words=[
              dict(en="Neither", ar="لا",      role="conj",    role_ar="أداة ربط",    note=""),
              dict(en="the",     ar="الـ",     role="article", role_ar="أداة",        note=""),
              dict(en="manager", ar="المدير",  role="noun",    role_ar="اسم",         note=""),
              dict(en="nor",     ar="ولا",     role="conj",    role_ar="أداة ربط",    note="★ شريكة neither"),
              dict(en="the",     ar="الـ",     role="article", role_ar="أداة",        note=""),
              dict(en="staff",   ar="الموظفون", role="noun",   role_ar="اسم",         note=""),
              dict(en="knew",    ar="عرفوا",   role="verb",    role_ar="فعل ماضي",    note=""),
            ],
            lesson="neither دائماً مع nor.",
            reveal="التوازي."),
       dict(kind="word_analysis", title="التوازي في القوائم",
            sentence="She likes swimming, reading, and traveling.",
            translation_ar="تحب السباحة والقراءة والسفر.",
            words=[
              dict(en="She",      ar="هي",       role="subject", role_ar="فاعل",   note=""),
              dict(en="likes",    ar="تحب",      role="verb",    role_ar="فعل",     note=""),
              dict(en="swimming", ar="السباحة",  role="verb",    role_ar="V-ing",   note="★ V-ing"),
              dict(en="reading",  ar="القراءة",  role="verb",    role_ar="V-ing",   note="★ V-ing"),
              dict(en="and",      ar="و",        role="conj",    role_ar="أداة ربط", note=""),
              dict(en="traveling", ar="السفر",   role="verb",    role_ar="V-ing",   note="★ V-ing"),
            ],
            lesson="التوازي: كل عناصر القائمة من نفس النوع.",
            reveal="الإشارات."),
       dict(kind="signals", title="كلمات الإشارة",
            items=[
              ("both →", "and"),
              ("neither →", "nor"),
              ("either →", "or"),
              ("not only →", "but also"),
              ("although / though +", "جملة كاملة (Subject + Verb)"),
              ("despite / in spite of +", "اسم أو V-ing"),
            ],
            reveal="فخاخ."),
       dict(kind="traps", title="فخاخ قياس",
            items=[
              ("both…or ❌", "الصح: both…and."),
              ("neither…or ❌", "الصح: neither…nor."),
              ("…reading, and to travel ❌", "خلل في التوازي، الصح: traveling."),
              ("although the rain ❌", "الصح: although it rained OR despite the rain."),
            ],
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة أدوات الربط",
            points=[
              "كل أداة مزدوجة لها شريك ثابت.",
              "العطف بنفس الصيغة (توازي).",
              "although + جملة كاملة. despite + اسم.",
            ]),
     ]),

dict(key="order", title="ترتيب الكلمات", subtitle="Word Order",
     icon="📐", theme=10, short="Subject + Verb + Object + Place + Time.",
     steps=[
       dict(kind="idea", title="الترتيب الأساسي",
            body="""ترتيب الكلمات في الإنجليزية ثابت — عكس العربية.

    الفاعل ← الفعل ← المفعول ← المكان ← الزمان.

    I drink coffee at home every morning.

المكان (at home) قبل الزمان (every morning).""",
            reveal="مثال."),
       dict(kind="word_analysis", title="الترتيب الكامل",
            sentence="I drink coffee at home every morning.",
            translation_ar="أشرب القهوة في البيت كل صباح.",
            words=[
              dict(en="I",             ar="أنا",     role="subject", role_ar="فاعل",   note="★ الفاعل أولاً"),
              dict(en="drink",         ar="أشرب",    role="verb",    role_ar="فعل",     note="★ الفعل ثانياً"),
              dict(en="coffee",        ar="قهوة",    role="object",  role_ar="مفعول",   note="★ المفعول ثالثاً"),
              dict(en="at",            ar="في",      role="prep",    role_ar="حرف جر",  note=""),
              dict(en="home",          ar="البيت",   role="noun",    role_ar="اسم",     note="★ المكان"),
              dict(en="every",         ar="كل",      role="adj",     role_ar="صفة",     note=""),
              dict(en="morning",       ar="صباح",    role="time",    role_ar="زمان",    note="★ الزمان آخراً"),
            ],
            lesson="S + V + O + Place + Time.",
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة ترتيب الكلمات",
            points=[
              "Subject + Verb + Object.",
              "المكان قبل الزمان.",
              "الصفة قبل الاسم (big house).",
              "الظرف يمكن أن يأتي في البداية للتأكيد (Yesterday, I went…).",
            ]),
     ]),

dict(key="punct", title="علامات الترقيم", subtitle="Punctuation",
     icon="✒️", theme=11, short="الفاصلة، النقطة، علامة الاستفهام، الفاصلة العليا.",
     steps=[
       dict(kind="idea", title="الترقيم — بسيط لكن مهم",
            body="""علامات الترقيم:

1) النقطة (.) — نهاية جملة خبرية.
2) علامة الاستفهام (?) — نهاية سؤال.
3) علامة التعجب (!) — انفعال.
4) الفاصلة (,) — فصل العناصر في قائمة.
5) الفاصلة العليا (') — للملكية أو للاختصارات.
6) الفاصلة المنقوطة (;) — بين جملتين مرتبطتين.

الأكثر اختباراً في STEP: فاصلة القائمة والفاصلة العليا.""",
            reveal="مثال."),
       dict(kind="word_analysis", title="فاصلة القائمة",
            sentence="I bought apples, oranges, and bananas.",
            translation_ar="اشتريت تفاحاً وبرتقالاً وموزاً.",
            words=[
              dict(en="I",       ar="أنا",      role="subject", role_ar="فاعل",       note=""),
              dict(en="bought",  ar="اشتريت",   role="verb",    role_ar="فعل ماضي",   note=""),
              dict(en="apples",  ar="تفاحاً",   role="noun",    role_ar="اسم",        note=""),
              dict(en=",",       ar="،",        role="punct",   role_ar="فاصلة",       note="★ فاصل القائمة"),
              dict(en="oranges", ar="برتقالاً", role="noun",    role_ar="اسم",        note=""),
              dict(en="and",     ar="و",        role="conj",    role_ar="أداة ربط",   note=""),
              dict(en="bananas", ar="موزاً",    role="noun",    role_ar="اسم",        note=""),
            ],
            lesson="فاصلة بين العناصر. and قبل الأخير.",
            reveal="الخلاصة."),
       dict(kind="summary", title="خلاصة الترقيم",
            points=[
              "فاصلة بين عناصر القائمة.",
              "الفاصلة قبل and اختيارية (لكن مفضلة).",
              "الفاصلة العليا للملكية (Ali's) أو الاختصار (it's).",
              "its (ملكية) ≠ it's (it is).",
            ]),
     ]),
]


# ============================================================
#  READING
# ============================================================
READING = [
dict(id=0, title="The History of Coffee", title_ar="تاريخ القهوة",
     theme=5, level="beginner",
     passage="""Coffee is one of the most popular drinks in the world. Every day, people drink more than two billion cups of coffee. But where did this popular drink come from?

According to legend, coffee was discovered in Ethiopia in the 9th century. A goat herder named Kaldi noticed that his goats became very energetic after eating red berries from a certain tree. He tried the berries himself and felt the same effect. He shared his discovery with local monks, who began using the berries to stay awake during long prayers.

From Ethiopia, coffee spread to Yemen and then to the rest of the Middle East. By the 15th century, coffee houses had become popular in cities like Cairo, Mecca, and Istanbul. These coffee houses were more than just places to drink coffee. They were meeting places where people discussed politics, shared news, and listened to music.

In the 17th century, coffee reached Europe. At first, some people were suspicious of the new drink. They called it "the bitter invention of Satan." However, the Pope tried it himself and enjoyed it, so coffee became accepted. Coffee houses soon opened in London, Paris, and Vienna.

Today, coffee is grown in more than 50 countries. Brazil is the largest producer, followed by Vietnam and Colombia. The coffee industry supports the lives of over 100 million people around the world.""",
     translation="القهوة من أكثر المشروبات شعبية في العالم. كل يوم، يشرب الناس أكثر من ملياري كوب من القهوة. لكن من أين جاء هذا المشروب الشهير؟ حسب الأسطورة، تم اكتشاف القهوة في إثيوبيا في القرن التاسع. لاحظ راعٍ للماعز اسمه كالدي أن ماعزه أصبحت نشيطة جداً بعد أكل حبوب حمراء من شجرة معينة. جرّب الحبوب بنفسه وشعر بنفس التأثير. شارك اكتشافه مع رهبان محليين، الذين بدأوا يستخدمون الحبوب للبقاء مستيقظين خلال الصلوات الطويلة. من إثيوبيا، انتشرت القهوة إلى اليمن ثم إلى بقية الشرق الأوسط. بحلول القرن الخامس عشر، أصبحت المقاهي شائعة في مدن مثل القاهرة ومكة وإسطنبول. لم تكن هذه المقاهي مجرد أماكن لشرب القهوة. كانت أماكن لقاء يناقش فيها الناس السياسة، ويتشاركون الأخبار، ويستمعون للموسيقى. في القرن السابع عشر، وصلت القهوة إلى أوروبا. في البداية، كان بعض الناس متشككين في المشروب الجديد. أسموه (اختراع الشيطان المر). ومع ذلك، جرّبه البابا بنفسه واستمتع به، فأصبحت القهوة مقبولة. سرعان ما فُتحت المقاهي في لندن وباريس وفيينا. اليوم، تُزرع القهوة في أكثر من 50 دولة. البرازيل هي أكبر منتج، تليها فيتنام وكولومبيا. تدعم صناعة القهوة حياة أكثر من 100 مليون شخص حول العالم.",
     questions=[
       dict(stem="What is the main idea of the passage?",
            opts=["The health benefits of coffee.",
                  "The history and global spread of coffee.",
                  "How to grow coffee in Ethiopia.",
                  "Different types of coffee drinks."], ans=1,
            expl="الفكرة الرئيسية: تاريخ القهوة وانتشارها.",
            tr="الفكرة: تاريخ القهوة."),
       dict(stem="According to the legend, who discovered coffee?",
            opts=["A European king", "A goat herder named Kaldi",
                  "Monks in Cairo", "The Pope"], ans=1,
            expl="النص: 'A goat herder named Kaldi'.",
            tr="راعي غنم اسمه كالدي."),
       dict(stem="The word 'spread' in paragraph 3 is closest in meaning to:",
            opts=["stopped", "expanded", "disappeared", "returned"], ans=1,
            expl="spread = انتشر = expanded.",
            tr="spread = expanded."),
       dict(stem="Which country is the largest producer of coffee today?",
            opts=["Vietnam", "Colombia", "Brazil", "Ethiopia"], ans=2,
            expl="النص: 'Brazil is the largest producer'.",
            tr="أكبر منتج: البرازيل."),
     ]),
dict(id=1, title="Why We Dream", title_ar="لماذا نحلم",
     theme=1, level="medium",
     passage="""Every night, when we fall asleep, our brains create strange and vivid worlds. We dream for about two hours every night, but scientists are still not sure exactly why we dream.

One popular theory is that dreams help us process our emotions. When we experience something difficult during the day, our brains replay these events at night in different forms. This may help us understand our feelings and reduce stress.

Another theory suggests that dreams are the brain's way of rehearsing for real-life situations. For example, if you dream about being chased, your brain may be practicing how to escape danger. In this view, dreams are like a training ground for survival.

A third theory is that dreams help with memory. Studies show that people who get enough sleep remember what they learned better than people who do not. During REM sleep — the stage when most dreaming happens — the brain sorts through information and decides what to keep and what to forget.

Some scientists believe that dreams have no real purpose at all. They think dreams are just random images that the brain creates while it is resting. According to this view, dreams are like the noise a computer makes when it is running in the background.

Whatever the truth is, one thing is certain: dreams have fascinated humans for thousands of years. Ancient Egyptians believed dreams were messages from the gods. Today, we know that dreams come from our own minds, but the mystery of why we dream remains unsolved.""",
     translation="كل ليلة، عندما ننام، يخلق دماغنا عوالم غريبة وحيوية. نحلم لمدة ساعتين كل ليلة، لكن العلماء لا يزالون غير متأكدين من سبب الحلم. هناك نظرية شائعة تقول إن الأحلام تساعدنا على معالجة عواطفنا. عندما نواجه شيئاً صعباً خلال النهار، يعيد دماغنا تشغيل هذه الأحداث في الليل بأشكال مختلفة. قد يساعدنا هذا على فهم مشاعرنا وتقليل التوتر. تقترح نظرية أخرى أن الأحلام هي طريقة الدماغ للتدرب على مواقف الحياة الواقعية. مثلاً، إذا حلمت بأنك مُطارَد، فقد يتدرب دماغك على كيفية الهروب من الخطر. من هذا المنظور، الأحلام مثل أرض تدريب للبقاء. نظرية ثالثة تقول إن الأحلام تساعد في الذاكرة. تُظهر الدراسات أن الأشخاص الذين يحصلون على نوم كافٍ يتذكرون ما تعلموه أفضل من الذين لا يحصلون عليه. خلال نوم حركة العين السريعة (REM) — المرحلة التي يحدث فيها معظم الحلم — يرتب الدماغ المعلومات ويقرر ما يحتفظ به وما ينساه. يعتقد بعض العلماء أن الأحلام ليس لها غرض حقيقي على الإطلاق. يعتقدون أن الأحلام مجرد صور عشوائية يخلقها الدماغ أثناء الراحة. وفقاً لهذا الرأي، الأحلام مثل الضجيج الذي يصدره الحاسوب عندما يعمل في الخلفية. أياً كانت الحقيقة، شيء واحد مؤكد: الأحلام أسرت البشر لآلاف السنين. اعتقد المصريون القدماء أن الأحلام رسائل من الآلهة. اليوم، نعرف أن الأحلام تأتي من عقولنا، لكن لغز سبب الحلم لا يزال غير محلول.",
     questions=[
       dict(stem="What is the main purpose of the passage?",
            opts=["To explain why people have nightmares.",
                  "To present several theories about why we dream.",
                  "To describe ancient Egyptian beliefs.",
                  "To argue that dreams have no purpose."], ans=1,
            expl="النص يعرض عدة نظريات.",
            tr="عرض نظريات عن الحلم."),
       dict(stem="According to one theory, dreams help us:",
            opts=["learn new languages", "process our emotions",
                  "predict the future", "forget bad memories"], ans=1,
            expl="النص: 'dreams help us process our emotions'.",
            tr="معالجة المشاعر."),
       dict(stem="The word 'rehearsing' is closest in meaning to:",
            opts=["practicing", "avoiding", "forgetting", "watching"], ans=0,
            expl="rehearsing = practicing = يتدرب.",
            tr="rehearsing = practicing."),
       dict(stem="What can be inferred about REM sleep?",
            opts=["It only happens in the morning.",
                  "It is important for memory.",
                  "It has no connection to dreams.",
                  "It happens only in children."], ans=1,
            expl="النص يربط REM بالذاكرة.",
            tr="REM مهم للذاكرة."),
     ]),
dict(id=2, title="The Great Barrier Reef", title_ar="الحاجز المرجاني العظيم",
     theme=4, level="medium",
     passage="""The Great Barrier Reef is the largest coral reef system in the world. It stretches for over 2,300 kilometers along the coast of Queensland, Australia. It is so large that astronauts can see it from space.

The reef is made up of billions of tiny animals called coral polyps. Each polyp is smaller than a grain of rice, but together they build structures that can be seen from space. Coral gets its bright colors from algae that live inside its tissues. The algae provide food for the coral, and the coral gives the algae a safe place to live. This relationship is called symbiosis.

The Great Barrier Reef is home to an incredible variety of life. It supports over 1,500 species of fish, 400 species of hard coral, and 30 species of whales and dolphins. It is also important for humans. Millions of tourists visit the reef every year, and the reef supports thousands of jobs in tourism and fishing.

However, the reef is in danger. Rising ocean temperatures caused by climate change are killing the coral. When the water gets too warm, the coral expels the algae that live inside it and turns white. This is called coral bleaching. If the water stays warm for too long, the coral dies.

Scientists are working hard to save the reef. Some are growing new coral in laboratories and transplanting it back to the reef. Others are studying which types of coral are more resistant to heat. Without action, the Great Barrier Reef may disappear within our lifetime.""",
     translation="الحاجز المرجاني العظيم هو أكبر نظام شعاب مرجانية في العالم. يمتد لأكثر من 2300 كيلومتر على طول ساحل كوينزلاند، أستراليا. إنه كبير جداً لدرجة أن رواد الفضاء يمكنهم رؤيته من الفضاء. تتكون الشعبة من مليارات الحيوانات الصغيرة المسماة بالزوائد المرجانية. كل زائدة أصغر من حبة الأرز، لكن معاً يبنون هياكل يمكن رؤيتها من الفضاء. يحصل المرجان على ألوانه الزاهية من الطحالب التي تعيش داخل أنسجته. توفر الطحالب الغذاء للمرجان، ويعطيه المرجان مكاناً آمناً للعيش. تُسمى هذه العلاقة بالتكافل. الحاجز المرجاني العظيم موطن لتنوع مذهل من الحياة. يدعم أكثر من 1500 نوع من الأسماك، و400 نوع من المرجان الصلب، و30 نوعاً من الحيتان والدلافين. كما أنه مهم للبشر. يزوره ملايين السياح كل عام، ويدعم آلاف الوظائف في السياحة والصيد. ومع ذلك، الحاجز في خطر. ارتفاع درجات حرارة المحيط الناتج عن تغير المناخ يقتل المرجان. عندما تصبح المياه دافئة جداً، يطرد المرجان الطحالب التي تعيش داخله ويتحول إلى اللون الأبيض. يُسمى هذا ابيضاض المرجان. إذا بقيت المياه دافئة لفترة طويلة، يموت المرجان. يعمل العلماء بجد لإنقاذ الشعبة. البعض يزرع مرجاناً جديداً في المختبرات ويعيد زرعه في الشعبة. آخرون يدرسون أنواع المرجان الأكثر مقاومة للحرارة. بدون اتخاذ إجراء، قد يختفي الحاجز المرجاني العظيم في حياتنا.",
     questions=[
       dict(stem="What is the main idea of the passage?",
            opts=["The beauty of Australian beaches.",
                  "The importance and threats facing the Great Barrier Reef.",
                  "How coral polyps reproduce.",
                  "Why astronauts study the ocean."], ans=1,
            expl="النص: أهمية الشعبة + التهديدات.",
            tr="أهمية + تهديدات."),
       dict(stem="What is 'symbiosis' according to the passage?",
            opts=["A type of coral polyp.",
                  "A relationship where two species help each other.",
                  "A disease that kills coral.",
                  "A method for transplanting coral."], ans=1,
            expl="النص: الطحالب والمرجان يتبادلان المنفعة.",
            tr="تكافل = تبادل منفعة."),
       dict(stem="What causes coral bleaching?",
            opts=["Too many tourists", "Cold ocean water",
                  "Rising ocean temperatures", "Loud noises"], ans=2,
            expl="النص: 'Rising ocean temperatures'.",
            tr="ارتفاع حرارة المحيط."),
       dict(stem="Which of the following is NOT mentioned as supporting the reef?",
            opts=["Tourism jobs", "Species of fish", "Species of whales", "Oil companies"], ans=3,
            expl="شركات النفط لم تُذكر.",
            tr="لم يُذكر: شركات النفط."),
     ]),
dict(id=3, title="The Psychology of Habits", title_ar="سيكولوجية العادات",
     theme=2, level="harder",
     passage="""Habits shape our lives more than we realize. From the moment we wake up to the moment we go to sleep, most of our actions are driven by habits. Scientists estimate that about 40 percent of our daily behaviors are habits rather than conscious decisions.

So how do habits form? Research shows that habits develop through a three-step loop: cue, routine, and reward. First, a cue triggers the brain to begin a behavior. Then, the behavior itself — the routine — takes place. Finally, a reward tells the brain whether this loop is worth remembering. Over time, the loop becomes automatic.

One of the most important discoveries about habits is that they never truly disappear. Once a habit is formed, it stays in the brain forever. This explains why people who quit smoking sometimes return to the habit years later. The habit loop is still there, waiting for the right cue to activate it.

To change a habit, scientists say you should not try to eliminate it. Instead, you should keep the same cue and the same reward, but replace the routine. For example, if you want to stop snacking when you are stressed, you can replace the snack with a short walk. The cue (stress) and the reward (relief) stay the same, but the routine changes.

Understanding habits is important for students. If you can build good study habits — like studying at the same time every day — you will find that learning becomes easier. The brain loves patterns, and good habits can make difficult tasks feel effortless over time.""",
     translation="تشكّل العادات حياتنا أكثر مما ندرك. من لحظة استيقاظنا إلى لحظة نومنا، معظم أفعالنا مدفوعة بالعادات. يقدر العلماء أن حوالي 40 بالمئة من سلوكياتنا اليومية هي عادات وليست قرارات واعية. فكيف تتشكل العادات؟ تُظهر الأبحاث أن العادات تتطور عبر حلقة من ثلاث خطوات: الإشارة، الروتين، والمكافأة. أولاً، تُشغّل الإشارة الدماغ لبدء سلوك. ثم يحدث السلوك نفسه — الروتين. أخيراً، تخبر المكافأة الدماغ ما إذا كانت هذه الحلقة تستحق التذكر. مع الوقت، تصبح الحلقة تلقائية. من أهم الاكتشافات عن العادات أنها لا تختفي حقاً. بمجرد تكوين عادة، تبقى في الدماغ للأبد. هذا يفسر لماذا يعود المدخنون أحياناً للعادة بعد سنوات. حلقة العادة ما زالت هناك، تنتظر الإشارة المناسبة لتنشيطها. لتغيير عادة، يقول العلماء إنه لا يجب أن تحاول إزالتها. بدلاً من ذلك، حافظ على نفس الإشارة ونفس المكافأة، لكن استبدل الروتين. مثلاً، إذا أردت التوقف عن تناول الوجبات الخفيفة عندما تكون متوتراً، يمكنك استبدال الوجبة الخفيفة بنزهة قصيرة. الإشارة (التوتر) والمكافأة (الارتياح) تبقى كما هي، لكن الروتين يتغير. فهم العادات مهم للطلاب. إذا بنيت عادات دراسية جيدة — مثل الدراسة في نفس الوقت كل يوم — ستجد أن التعلم يصبح أسهل. يحب الدماغ الأنماط، والعادات الجيدة يمكن أن تجعل المهام الصعبة تبدو سهلة مع الوقت.",
     questions=[
       dict(stem="What percentage of daily behaviors are habits according to the passage?",
            opts=["About 10%", "About 25%", "About 40%", "About 75%"], ans=2,
            expl="النص: 'about 40 percent'.",
            tr="40%."),
       dict(stem="What are the three steps of the habit loop?",
            opts=["Think, act, forget", "Cue, routine, reward",
                  "Start, stop, restart", "See, hear, feel"], ans=1,
            expl="النص: 'cue, routine, and reward'.",
            tr="إشارة، روتين، مكافأة."),
       dict(stem="According to scientists, how can you change a habit?",
            opts=["By removing all cues",
                  "By keeping the cue and reward but changing the routine",
                  "By ignoring the habit completely",
                  "By repeating the habit more often"], ans=1,
            expl="النص: 'keep the same cue and the same reward, but replace the routine'.",
            tr="غيّر الروتين فقط."),
       dict(stem="The word 'effortless' is closest in meaning to:",
            opts=["very difficult", "without effort", "slowly", "carefully"], ans=1,
            expl="effortless = بدون مجهود.",
            tr="بدون مجهود."),
     ]),
dict(id=4, title="How Photosynthesis Works", title_ar="كيف يعمل البناء الضوئي",
     theme=4, level="medium",
     passage="""Photosynthesis is the process by which plants make their own food. It is one of the most important chemical reactions on Earth because it produces the oxygen that almost all living things need to survive.

The process happens in the leaves of plants. Inside the leaves, there are tiny structures called chloroplasts. Chloroplasts contain a green pigment called chlorophyll. This pigment is what gives plants their green color, and it is also what captures energy from sunlight.

During photosynthesis, three things are needed: sunlight, water, and carbon dioxide. The plant takes in water through its roots and carbon dioxide through small openings in its leaves called stomata. Using the energy from sunlight, the plant combines water and carbon dioxide to create glucose, a type of sugar. Oxygen is released as a byproduct.

The glucose produced during photosynthesis is used by the plant for energy and growth. Some of it is stored as starch for later use. When humans and animals eat plants, they consume this stored energy. In this way, photosynthesis is the foundation of almost every food chain on the planet.

Photosynthesis also plays a critical role in regulating Earth's climate. By absorbing carbon dioxide from the atmosphere, plants help reduce the greenhouse effect. This is why protecting forests is one of the most effective ways to fight climate change.""",
     translation="البناء الضوئي هو العملية التي تصنع بها النباتات غذاءها. إنه أحد أهم التفاعلات الكيميائية على الأرض لأنه ينتج الأكسجين الذي تحتاجه جميع الكائنات الحية تقريباً للبقاء. تحدث العملية في أوراق النباتات. داخل الأوراق، توجد هياكل صغيرة تسمى البلاستيدات الخضراء. تحتوي البلاستيدات الخضراء على صبغة خضراء تسمى الكلوروفيل. هذه الصبغة هي ما يعطي النباتات لونها الأخضر، وهي أيضاً ما يلتقط الطاقة من ضوء الشمس. خلال البناء الضوئي، هناك حاجة لثلاثة أشياء: ضوء الشمس، الماء، وثاني أكسيد الكربون. يأخذ النبات الماء من خلال جذوره وثاني أكسيد الكربون من خلال فتحات صغيرة في أوراقه تسمى الثغور. باستخدام الطاقة من ضوء الشمس، يجمع النبات الماء وثاني أكسيد الكربون لإنشاء الجلوكوز، وهو نوع من السكر. يُطلق الأكسجين كناتج ثانوي. يُستخدم الجلوكوز المُنتج خلال البناء الضوئي من قبل النبات للطاقة والنمو. يُخزّن بعضه كنشا لاستخدامه لاحقاً. عندما يأكل البشر والحيوانات النباتات، يستهلكون هذه الطاقة المخزنة. بهذه الطريقة، البناء الضوئي هو أساس كل سلسلة غذائية على الكوكب. يلعب البناء الضوئي أيضاً دوراً حاسماً في تنظيم مناخ الأرض. بامتصاص ثاني أكسيد الكربون من الغلاف الجوي، تساعد النباتات في تقليل ظاهرة الاحتباس الحراري. لهذا السبب حماية الغابات هي واحدة من أكثر الطرق فعالية لمكافحة تغير المناخ.",
     questions=[
       dict(stem="What is the main idea of the passage?",
            opts=["The role of chlorophyll in plant color.",
                  "The process and importance of photosynthesis.",
                  "How forests affect weather.",
                  "The difference between glucose and starch."], ans=1,
            expl="النص يجمع العملية والأهمية.",
            tr="العملية + الأهمية."),
       dict(stem="What are the three things needed for photosynthesis?",
            opts=["Sunlight, water, oxygen", "Sunlight, water, carbon dioxide",
                  "Water, oxygen, sugar", "Carbon dioxide, oxygen, heat"], ans=1,
            expl="النص: 'sunlight, water, and carbon dioxide'.",
            tr="ضوء + ماء + CO2."),
       dict(stem="The word 'byproduct' is closest in meaning to:",
            opts=["main result", "secondary result", "fuel", "waste product"], ans=1,
            expl="byproduct = ناتج ثانوي.",
            tr="ناتج ثانوي."),
       dict(stem="Why is protecting forests important according to the passage?",
            opts=["Because forests are beautiful.",
                  "Because forests absorb carbon dioxide.",
                  "Because forests produce water.",
                  "Because forests block sunlight."], ans=1,
            expl="النص: 'absorbing carbon dioxide'.",
            tr="تمتص CO2."),
     ]),
dict(id=5, title="The Silk Road", title_ar="طريق الحرير",
     theme=6, level="medium",
     passage="""The Silk Road was not a single road but a network of trade routes that connected China to the Mediterranean Sea. For over 1,500 years, these routes served as the main channel for cultural, commercial, and technological exchange between East and West.

The Silk Road began around 130 BCE during the Han Dynasty in China. It stretched over 6,400 kilometers across mountains, deserts, and steppes. Merchants traveling along the Silk Road did not usually travel the entire route. Instead, goods were passed from one merchant to another, changing hands many times before reaching their final destination.

The most famous good traded on the Silk Road was, of course, silk. China had a monopoly on silk production for centuries, and the precious fabric was worth its weight in gold in Rome. But silk was not the only item traded. Merchants also traded spices, tea, porcelain, glass, gold, and horses.

Perhaps more important than the goods was the exchange of ideas. Buddhism traveled from India to China along the Silk Road. Papermaking, gunpowder, and the compass all spread from China to the West. In return, the East learned about Western astronomy, medicine, and mathematics.

The Silk Road declined in the 15th century. New sea routes made overland travel less profitable, and political instability in Central Asia made the roads dangerous. Yet the legacy of the Silk Road lives on. It reminds us that cultural exchange has always been a driving force behind human progress.""",
     translation="لم يكن طريق الحرير طريقاً واحداً، بل شبكة من الطرق التجارية التي ربطت الصين بالبحر المتوسط. لأكثر من 1500 عام، كانت هذه الطرق القناة الرئيسية للتبادل الثقافي والتجاري والتقني بين الشرق والغرب. بدأ طريق الحرير حوالي 130 قبل الميلاد خلال أسرة هان في الصين. امتد لأكثر من 6400 كيلومتر عبر الجبال والصحاري والسهوب. لم يسافر التجار على طول الطريق كاملاً عادة. بدلاً من ذلك، كانت البضائع تنتقل من تاجر إلى آخر، وتغيّر الأيدي عدة مرات قبل الوصول إلى وجهتها النهائية. البضاعة الأكثر شهرة التي تم تداولها على طريق الحرير كانت، بطبيعة الحال، الحرير. احتكرت الصين إنتاج الحرير لقرون، وكان القماش الثمين يساوي وزنه ذهباً في روما. لكن الحرير لم يكن البضاعة الوحيدة. كما تاجر التجار بالتوابل والشاي والخزف والزجاج والذهب والخيول. وربما كان تبادل الأفكار أكثر أهمية من البضائع. انتشرت البوذية من الهند إلى الصين على طول طريق الحرير. وانتشرت صناعة الورق والبارود والبوصلة من الصين إلى الغرب. وفي المقابل، تعلم الشرق عن علم الفلك والطب والرياضيات الغربية. تراجع طريق الحرير في القرن الخامس عشر. جعلت الطرق البحرية الجديدة السفر البري أقل ربحية، وأدى عدم الاستقرار السياسي في آسيا الوسطى إلى جعل الطرق خطيرة. ومع ذلك، فإن إرث طريق الحرير لا يزال حياً. يذكرنا بأن التبادل الثقافي كان دائماً قوة دافعة وراء التقدم البشري.",
     questions=[
       dict(stem="What is the main idea of the passage?",
            opts=["The history and legacy of the Silk Road.",
                  "How silk is produced in China.",
                  "Why sea travel replaced land travel.",
                  "The geography of Central Asia."], ans=0,
            expl="النص: تاريخ طريق الحرير وإرثه.",
            tr="التاريخ + الإرث."),
       dict(stem="According to the passage, why did the Silk Road decline?",
            opts=["Because silk became less valuable.",
                  "Because new sea routes and political instability.",
                  "Because merchants stopped trading.",
                  "Because the Han Dynasty fell."], ans=1,
            expl="النص يذكر السببين.",
            tr="طرق بحرية + عدم استقرار."),
       dict(stem="What does the word 'legacy' mean in the last paragraph?",
            opts=["End", "Something passed down from the past",
                  "A type of trade", "A Chinese city"], ans=1,
            expl="legacy = إرث.",
            tr="إرث."),
       dict(stem="Which of the following is NOT mentioned as traded on the Silk Road?",
            opts=["Silk", "Spices", "Gold", "Coffee"], ans=3,
            expl="القهوة لم تُذكر.",
            tr="القهوة لم تُذكر."),
     ]),
]


# ============================================================
#  LISTENING
# ============================================================
LISTENING = [
dict(id=0, title="A University Orientation", title_ar="تعريف الجامعة",
     theme=6, level="beginner",
     description="محادثة بين طالب جديد وموظفة في الجامعة — أصوات مختلفة تلقائياً.",
     script="""[VOICE: narrator, male, calm]
You will hear a conversation between a new student and a university staff member.
[PAUSE 2s]

[VOICE: female, friendly, professional]
Hi! Welcome to King Saud University. How can I help you today?
[PAUSE 2s]

[VOICE: male, young]
Hi, yes. I'm looking for the science building. Could you tell me where it is?
[PAUSE 2s]

[VOICE: female, friendly, professional]
Of course. The science building is on the north side of campus. Just walk straight from here, and after about five minutes you'll see it on your left — a tall white building.
[PAUSE 2s]

[VOICE: male, young]
Great, thank you. One more thing — do you know when the first lecture starts?
[PAUSE 2s]

[VOICE: female, friendly, professional]
The first lectures usually start at 8 AM. But you should check your schedule carefully. Each college has different timings.
[PAUSE 2s]

[VOICE: male, young]
Okay, I will. Thanks for your help!
[PAUSE 2s]

[VOICE: female, friendly, professional]
You're welcome. And welcome to the university!""",
     questions=[
       dict(stem="Where is the student going?",
            opts=["The library", "The science building",
                  "The cafeteria", "The administration office"], ans=1,
            expl="الطالب: 'I'm looking for the science building'.", tr="مبنى العلوم."),
       dict(stem="How long does it take to walk to the science building?",
            opts=["About 2 minutes", "About 5 minutes",
                  "About 10 minutes", "About 20 minutes"], ans=1,
            expl="'after about five minutes'.", tr="٥ دقائق."),
       dict(stem="When do the first lectures usually start?",
            opts=["7 AM", "8 AM", "9 AM", "10 AM"], ans=1,
            expl="'usually start at 8 AM'.", tr="٨ صباحاً."),
       dict(stem="What does the staff member recommend the student do?",
            opts=["Arrive early", "Check the schedule carefully",
                  "Call the office", "Take a taxi"], ans=1,
            expl="'check your schedule carefully'.", tr="راجع الجدول بعناية."),
     ]),
dict(id=1, title="Weather Report for Riyadh", title_ar="تقرير الطقس للرياض",
     theme=5, level="beginner",
     description="نشرة جوية يومية — صوت مذيع واحد أنثى.",
     script="""[VOICE: narrator, male]
You will hear a weather report for the city of Riyadh.
[PAUSE 2s]

[VOICE: female, professional, host]
Good morning. This is your weather report for Riyadh for today, Tuesday, October 15th.
[PAUSE 1s]
Currently, the temperature is 28 degrees Celsius, and the sky is clear. Winds are light, coming from the northwest at about 10 kilometers per hour.
[PAUSE 1s]
This afternoon, expect temperatures to rise to a high of 35 degrees. The humidity will remain low, around 15 percent. There is no chance of rain today.
[PAUSE 1s]
Looking ahead to tomorrow, Wednesday, temperatures will drop slightly to a high of 32 degrees. However, strong winds are expected in the evening, with speeds reaching up to 40 kilometers per hour. If you have outdoor plans tomorrow evening, you may want to consider changing them.
[PAUSE 1s]
That's your weather update. Stay cool, Riyadh!""",
     questions=[
       dict(stem="What is the current temperature in Riyadh?",
            opts=["20°C", "28°C", "35°C", "40°C"], ans=1,
            expl="'28 degrees Celsius'.", tr="٢٨°."),
       dict(stem="What will the high temperature be this afternoon?",
            opts=["28°C", "30°C", "32°C", "35°C"], ans=3,
            expl="'a high of 35 degrees'.", tr="٣٥°."),
       dict(stem="What is expected tomorrow evening?",
            opts=["Heavy rain", "Strong winds", "Snow", "High humidity"], ans=1,
            expl="'strong winds are expected in the evening'.", tr="رياح قوية."),
       dict(stem="What is the humidity expected to be?",
            opts=["Around 15%", "Around 30%", "Around 50%", "Around 80%"], ans=0,
            expl="'around 15 percent'.", tr="١٥٪."),
     ]),
dict(id=2, title="A Job Interview", title_ar="مقابلة عمل",
     theme=8, level="medium",
     description="مقابلة بين مدير (رجل) ومتقدم (رجل شاب) — صوتان مختلفان.",
     script="""[VOICE: narrator, female, calm]
You will hear a job interview between a manager and an applicant.
[PAUSE 2s]

[VOICE: male, professional]
Good morning, Mr. Al-Otaibi. Please, have a seat. Thank you for coming in today.
[PAUSE 2s]

[VOICE: male, young]
Good morning. Thank you for the opportunity.
[PAUSE 2s]

[VOICE: male, professional]
So, tell me a little about your experience. I see on your CV that you worked at Aramco for three years.
[PAUSE 2s]

[VOICE: male, young]
Yes, that's correct. I worked in the marketing department. My main responsibility was managing social media campaigns for our products. I also worked closely with the design team to create digital advertisements.
[PAUSE 2s]

[VOICE: male, professional]
Interesting. And why did you leave?
[PAUSE 2s]

[VOICE: male, young]
I wanted to grow professionally. Aramco is a great company, but I felt I was ready for a new challenge in a smaller, more dynamic environment.
[PAUSE 2s]

[VOICE: male, professional]
I understand. One final question — what do you know about our company?
[PAUSE 2s]

[VOICE: male, young]
I know your company was founded in 2015 and focuses on sustainable technology. I have followed your work in solar energy for a while, and I'm very impressed by your recent project in NEOM.
[PAUSE 2s]

[VOICE: male, professional]
Very good. We'll be in touch by the end of the week.""",
     questions=[
       dict(stem="Where did the applicant work before?",
            opts=["SABIC", "Aramco", "STC", "NEOM"], ans=1,
            expl="'you worked at Aramco'.", tr="أرامكو."),
       dict(stem="What was the applicant's main responsibility?",
            opts=["Managing social media campaigns", "Designing buildings",
                  "Hiring employees", "Selling cars"], ans=0,
            expl="'managing social media campaigns'.", tr="إدارة حملات السوشيال ميديا."),
       dict(stem="Why did the applicant leave his previous job?",
            opts=["He was fired", "He wanted a new challenge",
                  "He moved to another city", "He wanted higher salary"], ans=1,
            expl="'ready for a new challenge'.", tr="أراد تحدياً جديداً."),
       dict(stem="When will the manager contact him?",
            opts=["Tomorrow", "Next week", "By the end of the week", "In a month"], ans=2,
            expl="'by the end of the week'.", tr="بنهاية الأسبوع."),
     ]),
dict(id=3, title="A Podcast about Sleep", title_ar="بودكاست عن النوم",
     theme=2, level="harder",
     description="مقتطف من بودكاست علمي — صوت مذيعة أنثى محترفة.",
     script="""[VOICE: narrator, male, calm]
You will hear a short segment from a science podcast about sleep.
[PAUSE 2s]

[VOICE: female, host, warm]
Welcome back to "The Science Hour." I'm your host, Dr. Sarah Henderson. Today, we're talking about one of the most important — and most neglected — parts of our lives: sleep.
[PAUSE 1s]
Most adults need between seven and nine hours of sleep every night. But according to a recent study, nearly one in three adults gets less than six hours. This is a serious problem.
[PAUSE 1s]
Why is sleep so important? Because it's not just about resting. While you sleep, your brain is hard at work. It processes memories, cleans out toxins, and prepares you for the next day. Lack of sleep has been linked to heart disease, diabetes, and depression.
[PAUSE 1s]
Here are three simple tips for better sleep. First, try to go to bed and wake up at the same time every day — even on weekends. Second, avoid screens for at least one hour before bed. The blue light from phones and computers tricks your brain into thinking it's still daytime. Third, keep your bedroom cool and dark. Your body temperature needs to drop slightly for you to fall asleep.
[PAUSE 1s]
If you're struggling with sleep, don't ignore it. Talk to your doctor. Sleep is not a luxury — it's a necessity.""",
     questions=[
       dict(stem="According to the host, how many hours of sleep do most adults need?",
            opts=["4-5 hours", "5-6 hours", "7-9 hours", "10-12 hours"], ans=2,
            expl="'between seven and nine hours'.", tr="٧-٩ ساعات."),
       dict(stem="What does the brain do during sleep?",
            opts=["Nothing important", "Processes memories and cleans toxins",
                  "Stops working", "Only controls breathing"], ans=1,
            expl="'processes memories, cleans out toxins'.", tr="معالجة الذكريات + تنظيف السموم."),
       dict(stem="Why should you avoid screens before bed?",
            opts=["They are expensive", "Blue light tricks your brain",
                  "They cause eye disease", "They are too bright"], ans=1,
            expl="'blue light from phones and computers tricks your brain'.", tr="الضوء الأزرق يخدع الدماغ."),
       dict(stem="What does the host call sleep?",
            opts=["A luxury", "A necessity", "An option", "A problem"], ans=1,
            expl="'Sleep is not a luxury — it's a necessity'.", tr="النوم ضرورة."),
     ]),
dict(id=4, title="A Doctor's Appointment", title_ar="زيارة الطبيب",
     theme=2, level="beginner",
     description="محادثة بين طبيب (رجل) ومريضة (امرأة) — صوتان مختلفان تلقائياً.",
     script="""[VOICE: narrator, female, calm]
You will hear a conversation between a doctor and a patient.
[PAUSE 2s]

[VOICE: male, professional]
Good afternoon. Please, come in and have a seat. What brings you in today?
[PAUSE 2s]

[VOICE: female, young]
I've had a bad headache for the past three days, and I feel very tired.
[PAUSE 2s]

[VOICE: male, professional]
I see. Have you been sleeping well?
[PAUSE 2s]

[VOICE: female, young]
Not really. I've been working late every night this week.
[PAUSE 2s]

[VOICE: male, professional]
That could explain the headache. Are you drinking enough water during the day?
[PAUSE 2s]

[VOICE: female, young]
I don't think so. I usually forget to drink water when I'm busy.
[PAUSE 2s]

[VOICE: male, professional]
Alright. I recommend that you drink at least eight glasses of water a day, and try to sleep for seven to eight hours. If the headache continues after a week, come back and see me.
[PAUSE 2s]

[VOICE: female, young]
Thank you, doctor. I'll do that.""",
     questions=[
       dict(stem="What is the patient's main problem?",
            opts=["A stomach ache", "A bad headache",
                  "A broken arm", "A sore throat"], ans=1,
            expl="'I've had a bad headache'.", tr="صداع شديد."),
       dict(stem="Why has she been feeling tired?",
            opts=["She has been working late every night",
                  "She has been traveling",
                  "She has been sick",
                  "She has been exercising too much"], ans=0,
            expl="'working late every night this week'.", tr="تعمل لوقت متأخر."),
       dict(stem="What does the doctor recommend?",
            opts=["Take medicine and rest",
                  "Drink water and sleep more",
                  "See a specialist",
                  "Change her job"], ans=1,
            expl="'drink at least eight glasses of water... sleep for seven to eight hours'.", tr="ماء + نوم."),
       dict(stem="What should the patient do if the headache continues?",
            opts=["Take aspirin", "Come back after a week",
                  "Go to the hospital immediately", "Stop working"], ans=1,
            expl="'If the headache continues after a week, come back'.", tr="ترجع بعد أسبوع."),
     ]),
dict(id=5, title="A History Class", title_ar="حصة تاريخ",
     theme=4, level="medium",
     description="محاضرة جامعية عن الحضارات القديمة — صوت أستاذ رجل محترف.",
     script="""[VOICE: narrator, female, calm]
You will hear part of a university history lecture.
[PAUSE 2s]

[VOICE: male, professional]
Good morning, everyone. Today we're going to talk about one of the most fascinating civilizations in history — ancient Egypt.
[PAUSE 1s]
The Egyptian civilization lasted for over three thousand years, from around 3100 BCE to 30 BCE. That's longer than any other civilization in human history.
[PAUSE 1s]
One of the most remarkable achievements of the ancient Egyptians was their writing system, known as hieroglyphics. For centuries, no one could read hieroglyphics. Then, in 1799, French soldiers discovered a stone near the town of Rosetta. This stone, now known as the Rosetta Stone, contained the same text written in three different scripts. By comparing them, scholars were finally able to decode the ancient writing.
[PAUSE 1s]
Another achievement was their architecture. The Great Pyramid of Giza was built over 4,500 years ago, and it remained the tallest structure in the world for more than three thousand years. It was made of over two million stone blocks, each weighing several tons.
[PAUSE 1s]
For next week, please read chapter five on Egyptian religion. And remember — your essays are due in two weeks.""",
     questions=[
       dict(stem="How long did the Egyptian civilization last?",
            opts=["500 years", "1000 years", "3000 years", "5000 years"], ans=2,
            expl="'over three thousand years'.", tr="أكثر من ٣٠٠٠ سنة."),
       dict(stem="What was the Rosetta Stone used for?",
            opts=["Building the pyramids",
                  "Decoding hieroglyphics",
                  "Recording history",
                  "Measuring time"], ans=1,
            expl="'scholars were finally able to decode the ancient writing'.", tr="فك شفرة الكتابة."),
       dict(stem="How long did the Great Pyramid remain the tallest structure?",
            opts=["100 years", "500 years",
                  "More than 3000 years", "More than 5000 years"], ans=2,
            expl="'remained the tallest structure in the world for more than three thousand years'.", tr="أكثر من ٣٠٠٠ سنة."),
       dict(stem="What is due in two weeks?",
            opts=["A test", "An essay", "A presentation", "A project"], ans=1,
            expl="'your essays are due in two weeks'.", tr="المقالات."),
     ]),
]


# ============================================================
#  GRAMMAR QUESTIONS
# ============================================================
QUESTIONS = [
    (0, "The list of items ___ on the desk.", ["are", "is", "were", "be"], 1,
     "الفاعل list مفرد.", "The list of items **is** on the desk.", "قائمة الأغراض على الطاولة."),
    (0, "Each of the students ___ a laptop.", ["have", "has", "are having", "having"], 1,
     "each = مفرد.", "Each of the students **has** a laptop.", "كل طالب عنده لابتوب."),
    (0, "Neither Ali nor his brothers ___ coming.", ["is", "was", "are", "has"], 2,
     "الأقرب brothers → are.", "Neither Ali nor his brothers **are** coming.", "لا علي ولا إخوانه جايين."),
    (0, "A number of students ___ absent today.", ["is", "was", "are", "be"], 2,
     "a number of → جمع.", "A number of students **are** absent today.", "عدد من الطلاب غائبون."),
    (1, "She ___ in Riyadh since 2015.", ["lives", "lived", "has lived", "is living"], 2,
     "since → مضارع تام.", "She **has lived** in Riyadh since 2015.", "تعيش في الرياض من ٢٠١٥."),
    (1, "I ___ my homework before the teacher arrived.", ["finished", "had finished", "have finished", "finish"], 1,
     "الحدث الأسبق → had.", "I **had finished** before the teacher arrived.", "خلصت قبل وصوله."),
    (1, "They ___ TV when I called.", ["watched", "were watching", "watch", "have watched"], 1,
     "مستمر → were + V-ing.", "They **were watching** TV when I called.", "كانوا يشاهدون."),
    (1, "Look at those clouds! It ___ rain.", ["will", "is going to", "would", "shall"], 1,
     "دليل مرئي → going to.", "It **is going to** rain.", "ستمطر."),
    (2, "The report ___ by the manager yesterday.", ["wrote", "was written", "has wrote", "is writing"], 1,
     "ماضي مجهول.", "The report **was written** by the manager.", "التقرير كُتب."),
    (2, "English ___ in many countries.", ["speaks", "is spoken", "is speaking", "spoke"], 1,
     "is + V3.", "English **is spoken** in many countries.", "تُتحدَّث في دول."),
    (2, "The window ___ before we arrived.", ["was breaking", "had been broken", "has broke", "breaks"], 1,
     "had been + V3.", "The window **had been broken** before we arrived.", "كُسرت قبل وصولنا."),
    (3, "If you heat ice, it ___.", ["will melts", "melts", "melt", "melted"], 1,
     "حقائق → نوع 0.", "If you heat ice, it **melts**.", "الثلج يذوب."),
    (3, "If I ___ more time, I would travel.", ["have", "had", "will have", "would have"], 1,
     "نوع 2: if + ماضي.", "If I **had** more time, I would travel.", "لو عندي وقت."),
    (3, "If she had studied, she ___ the exam.", ["would pass", "would have passed", "will pass", "passes"], 1,
     "نوع 3: would have + V3.", "If she had studied, she **would have passed**.", "لو ذاكرت."),
    (4, "The man ___ car was stolen called the police.", ["who", "whom", "whose", "which"], 2,
     "بعده اسم → whose.", "The man **whose** car was stolen called the police.", "الرجل انسرقت سيارته."),
    (4, "This is the book ___ I told you about.", ["who", "that", "whose", "where"], 1,
     "شيء بدون فاصلة → that.", "This is the book **that** I told you about.", "الكتاب اللي أخبرتك."),
    (4, "That is the city ___ I was born.", ["which", "who", "where", "whose"], 2,
     "مكان → where.", "That is the city **where** I was born.", "المدينة اللي انولدت فيها."),
    (5, "The exam is ___ Sunday.", ["in", "on", "at", "by"], 1,
     "يوم → on.", "The exam is **on** Sunday.", "الاختبار يوم الأحد."),
    (5, "We will meet ___ 8 o'clock.", ["in", "on", "at", "to"], 2,
     "ساعة → at.", "We will meet **at** 8 o'clock.", "نتقابل الساعة ٨."),
    (5, "My uncle lives ___ Jeddah.", ["at", "on", "in", "to"], 2,
     "مدينة → in.", "My uncle lives **in** Jeddah.", "عمي يسكن في جدة."),
    (5, "She was born ___ March.", ["on", "in", "at", "by"], 1,
     "شهر → in.", "She was born **in** March.", "انولدت في مارس."),
    (6, "Sara forgot ___ book.", ["she", "her", "hers", "herself"], 1,
     "قبل اسم → her.", "Sara forgot **her** book.", "سارة نسيت كتابها."),
    (6, "The decision is ___, not yours.", ["my", "me", "mine", "I"], 2,
     "بدون اسم → mine.", "The decision is **mine**.", "القرار قراري."),
    (6, "He hurt ___ while playing.", ["him", "his", "himself", "he"], 2,
     "نفس الشخص → himself.", "He hurt **himself** while playing.", "أذى نفسه."),
    (7, "You ___ wear a seat belt; it is the law.", ["might", "must", "may", "could"], 1,
     "قانون → must.", "You **must** wear a seat belt.", "لازم تلبس الحزام."),
    (7, "You look tired. You ___ rest.", ["should", "to should", "must to", "can to"], 0,
     "نصيحة → should.", "You **should** rest.", "لازم ترتاح."),
    (7, "She ___ finish the report by Monday.", ["has to", "have to", "must to", "can to"], 0,
     "She مفرد → has to.", "She **has to** finish by Monday.", "لازم تخلّص."),
    (7, "You ___ come if you don't want to.", ["mustn't", "don't have to", "can't", "shouldn't"], 1,
     "غير لازم → don't have to.", "You **don't have to** come.", "مو لازم تجي."),
    (8, "How ___ water do you drink?", ["many", "much", "few", "a few"], 1,
     "غير معدود → much.", "How **much** water?", "كم ماء؟"),
    (8, "There are only ___ seats left.", ["a little", "much", "a few", "little"], 2,
     "معدود → a few.", "There are only **a few** seats left.", "باقي مقاعد قليلة."),
    (8, "I have ___ time, so I can't join.", ["few", "a few", "little", "many"], 2,
     "غير معدود سلبي → little.", "I have **little** time.", "عندي وقت قليل."),
    (8, "She gave me some useful ___.", ["advices", "advice", "an advice", "advices"], 1,
     "advice غير معدود.", "She gave me useful **advice**.", "أعطتني نصيحة."),
    (9, "He is both smart ___ hardworking.", ["or", "and", "nor", "but"], 1,
     "both → and.", "He is both smart **and** hardworking.", "ذكي ومجتهد."),
    (9, "Neither the manager ___ the staff knew.", ["or", "and", "nor", "but"], 2,
     "neither → nor.", "Neither the manager **nor** the staff knew.", "لا المدير ولا الموظفين."),
    (9, "She likes swimming, reading, and ___.", ["to travel", "travel", "traveling", "travels"], 2,
     "التوازي → V-ing.", "She likes swimming, reading, and **traveling**.", "تحب السباحة."),
    (9, "___ it was raining, we went out.", ["Despite", "Although", "Because", "So"], 1,
     "although + جملة.", "**Although** it was raining, we went out.", "مع إنها كانت تمطر."),
    (10, "Correct word order:", ["I every morning drink coffee.", "I drink coffee every morning.",
                                  "Drink I coffee every morning.", "Coffee I drink every morning."], 1,
     "Subject + Verb + Object + Time.", "I **drink coffee every morning**.", "أشرب القهوة كل صباح."),
    (11, "Which is correct?", ["I bought apples oranges and bananas.",
                                "I bought apples, oranges, and bananas.",
                                "I bought, apples oranges and bananas.",
                                "I bought apples oranges, and bananas."], 1,
     "فاصلة بين عناصر القائمة.", "I bought apples, oranges, and bananas.", "اشتريت تفاح وبرتقال وموز."),
]


DAYS = {1:[0,1], 2:[2,3], 3:[4,5], 4:[6,7], 5:[8,9], 6:[10,11], 7:[], 8:[]}
READING_DAYS = {1:[0], 2:[1], 3:[2], 4:[3], 5:[4], 6:[5], 7:[0,3,5], 8:[1,2,4]}
LISTENING_DAYS = {1:[0], 2:[1], 3:[2], 4:[3], 5:[4], 6:[5], 7:[0,4,5], 8:[1,2,3]}
EXTRA_TITLES = {7:"مراجعة نقاط الضعف", 8:"الاختبار النهائي"}


# ============================================================
#  DB
# ============================================================
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS user(id INTEGER PRIMARY KEY CHECK(id=1),
            name TEXT, target INTEGER, xp INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0, last_active TEXT, level INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS lessons(rid INTEGER PRIMARY KEY,
            done_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS drills(day INTEGER PRIMARY KEY,
            score INTEGER, passed INTEGER);
        CREATE TABLE IF NOT EXISTS attempts(day INTEGER, qid INTEGER,
            ok INTEGER DEFAULT 0, PRIMARY KEY(day, qid));
        CREATE TABLE IF NOT EXISTS vault(qid INTEGER PRIMARY KEY, day INTEGER,
            wrong INTEGER DEFAULT 1, streak INTEGER DEFAULT 0, mastered INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS step_progress(rid INTEGER, step_index INTEGER,
            done_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(rid, step_index));
        CREATE TABLE IF NOT EXISTS reading_progress(rid INTEGER PRIMARY KEY,
            done_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS reading_attempts(rid INTEGER, qid INTEGER,
            ok INTEGER DEFAULT 0, PRIMARY KEY(rid, qid));
        CREATE TABLE IF NOT EXISTS listening_progress(lid INTEGER PRIMARY KEY,
            done_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS listening_attempts(lid INTEGER, qid INTEGER,
            ok INTEGER DEFAULT 0, PRIMARY KEY(lid, qid));
        """)

def day_title(n):
    if DAYS[n]: return " + ".join(RULES[i]["title"] for i in DAYS[n])
    return EXTRA_TITLES.get(n, f"اليوم {n}")

def get_status(c):
    out, prev = {}, True
    for n in range(1, 9):
        rids = DAYS[n]
        done = sum(1 for r in rids if c.execute(
            "SELECT 1 FROM lessons WHERE rid=?", (r,)).fetchone())
        dp = bool(c.execute(
            "SELECT 1 FROM drills WHERE day=? AND passed=1", (n,)).fetchone())
        vo = c.execute(
            "SELECT COUNT(*) FROM vault WHERE day=? AND mastered=0", (n,)).fetchone()[0]
        rd = sum(1 for r in READING_DAYS.get(n, []) if c.execute(
            "SELECT 1 FROM reading_progress WHERE rid=?", (r,)).fetchone())
        ls = sum(1 for l in LISTENING_DAYS.get(n, []) if c.execute(
            "SELECT 1 FROM listening_progress WHERE lid=?", (l,)).fetchone())
        comp = (done == len(rids)) and dp and (vo == 0)
        out[n] = dict(n=n, title=day_title(n), unlocked=prev, complete=comp,
                      lessons=done, of=len(rids), drill=dp, vault=vo,
                      reading=rd, reading_of=len(READING_DAYS.get(n, [])),
                      listening=ls, listening_of=len(LISTENING_DAYS.get(n, [])))
        prev = comp
    return out

def get_threshold(c, n):
    u = c.execute("SELECT target FROM user").fetchone()
    return max(70, u["target"]) if (n == 8 and u) else 70

def pub_q(i):
    q = QUESTIONS[i]
    return dict(id=i, stem=q[1], opts=q[2])

def pub_reading(r):
    return dict(id=r["id"], title=r["title"], title_ar=r["title_ar"],
                theme=r["theme"], level=r["level"], passage=r["passage"],
                translation=r["translation"],
                questions=[dict(stem=q["stem"], opts=q["opts"]) for q in r["questions"]])

def pub_listening(l):
    return dict(id=l["id"], title=l["title"], title_ar=l["title_ar"],
                theme=l["theme"], level=l["level"],
                description=l["description"],
                questions=[dict(stem=q["stem"], opts=q["opts"]) for q in l["questions"]])

def drill_ids(c, n):
    allq = list(range(len(QUESTIONS)))
    if n in (1,2,3,4,5,6):
        ids = [i for i in allq if QUESTIONS[i][0] in DAYS[n]]
    elif n == 7:
        w = [r[0] for r in c.execute(
            "SELECT qid FROM vault ORDER BY mastered, wrong DESC")][:15]
        rest = [i for i in allq if i not in w]
        ids = w + random.sample(rest, max(0, 15 - len(w)))
    else:
        ids = random.sample(allq, 25)
    random.shuffle(ids)
    return ids

def add_xp(c, amount):
    c.execute("UPDATE user SET xp = xp + ? WHERE id=1", (amount,))
    row = c.execute("SELECT xp FROM user").fetchone()
    if row:
        lvl = max(1, (row["xp"] // 100) + 1)
        c.execute("UPDATE user SET level=? WHERE id=1", (lvl,))


# ============================================================
#  TTS
# ============================================================
def _cleanup_loop():
    while True:
        time.sleep(8)
        now = time.time()
        with TTS_LOCK:
            for path, ts in list(TTS_FILES.items()):
                if now - ts > TTS_MAX_AGE:
                    try: os.remove(path)
                    except Exception: pass
                    TTS_FILES.pop(path, None)
threading.Thread(target=_cleanup_loop, daemon=True).start()


def _edge_tts_save(text, voice, out_path, rate="-15%", pitch="+0Hz"):
    text = clean_tts_text(text)
    if not text:
        raise ValueError("empty text after clean")
    async def _run():
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(out_path)
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())
    finally:
        try: asyncio.set_event_loop(None)
        except Exception: pass
        loop.close()


def _make_silent_mp3(duration, out_path):
    if not FFMPEG_AVAILABLE:
        return False
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(duration), "-c:a", "libmp3lame", "-b:a", "48k", out_path
        ], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False


def _concat_mp3s(paths, out_path):
    if not paths:
        return False
    if len(paths) == 1:
        try:
            os.replace(paths[0], out_path)
            return True
        except Exception:
            pass
    if FFMPEG_AVAILABLE:
        try:
            list_file = out_path + ".txt"
            with open(list_file, "w") as f:
                for p in paths:
                    f.write(f"file '{os.path.abspath(p)}'\n")
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_file, "-c", "copy", out_path
            ], capture_output=True, timeout=60, check=True)
            os.remove(list_file)
            for p in paths:
                try: os.remove(p)
                except Exception: pass
            return True
        except Exception:
            try: os.remove(list_file)
            except Exception: pass
    try:
        with open(out_path, "wb") as out:
            for p in paths:
                with open(p, "rb") as f:
                    out.write(f.read())
        for p in paths:
            try: os.remove(p)
            except Exception: pass
        return True
    except Exception:
        return False


def _generate_with_pauses(text, voice, out_path, pause_ms=BLANK_PAUSE_MS, rate="-15%"):
    parts = split_at_blanks(text)
    parts = [clean_tts_text(p) for p in parts if clean_tts_text(p)]
    if not parts:
        raise ValueError("empty after split")
    if len(parts) <= 1:
        _edge_tts_save(parts[0] if parts else text, voice, out_path, rate=rate)
        return
    ts_base = f"{int(time.time()*1000)}_{random.randint(1000,9999)}"
    part_files = []
    for i, part in enumerate(parts):
        p = os.path.join(TTS_DIR, f"seg_tts_{ts_base}_{i}.mp3")
        try:
            _edge_tts_save(part, voice, p, rate=rate)
            if os.path.exists(p) and os.path.getsize(p) > 0:
                part_files.append(p)
        except Exception:
            pass
    if not part_files:
        raise ValueError("no parts generated")
    silence_file = os.path.join(TTS_DIR, f"sil_tts_{ts_base}.mp3")
    silence_ok = _make_silent_mp3(pause_ms / 1000.0, silence_file)
    ordered = []
    for i, pf in enumerate(part_files):
        ordered.append(pf)
        if i < len(part_files) - 1 and silence_ok:
            ordered.append(silence_file)
    if not _concat_mp3s(ordered, out_path):
        raise ValueError("concat failed")


def parse_script(script):
    chunks = []
    lines = script.split("\n")
    current_voice = "en-US-GuyNeural"
    pending_text = []
    def flush_text():
        nonlocal pending_text
        if pending_text:
            text = " ".join(pending_text).strip()
            if text:
                chunks.append({"type": "speech", "text": text, "voice": current_voice})
            pending_text = []
    for line in lines:
        t = line.strip()
        if not t:
            continue
        if t.startswith("[VOICE"):
            flush_text()
            current_voice = pick_voice(t)
        elif t.startswith("[PAUSE"):
            flush_text()
            m = re.search(r"([\d.]+)\s*s", t)
            dur = float(m.group(1)) if m else 1.0
            chunks.append({"type": "silence", "duration": dur})
        elif t.startswith("[SFX"):
            flush_text()
            chunks.append({"type": "silence", "duration": 0.6})
        else:
            pending_text.append(t)
    flush_text()
    return chunks


@app.get("/tts")
def api_tts():
    text = (request.args.get("t") or "").strip()[:600]
    lang = (request.args.get("lang") or "en").strip().lower()
    if not text:
        abort(404)
    if not text.strip():
        abort(404)
    voice = "ar-SA-HamedNeural" if lang == "ar" else "en-GB-RyanNeural"
    rate = "-10%" if lang == "ar" else "-15%"
    fname = f"tts_{int(time.time()*1000)}_{random.randint(1000,9999)}.mp3"
    out_path = os.path.join(TTS_DIR, fname)
    generated = False
    if EDGE_TTS_OK:
        try:
            _generate_with_pauses(text, voice, out_path, pause_ms=BLANK_PAUSE_MS, rate=rate)
            generated = os.path.exists(out_path) and os.path.getsize(out_path) > 0
        except Exception:
            generated = False
    if not generated and GTTS_OK:
        try:
            tts_text = clean_tts_text(text)
            tts_lang = "ar" if lang == "ar" else "en"
            buf = io.BytesIO()
            gTTS(tts_text, lang=tts_lang, slow=(lang=="en")).write_to_fp(buf)
            buf.seek(0)
            with open(out_path, "wb") as f:
                f.write(buf.read())
            generated = True
        except Exception:
            generated = False
    if not generated:
        abort(503)
    with TTS_LOCK:
        TTS_FILES[out_path] = time.time()
    resp = send_file(out_path, mimetype="audio/mpeg", as_attachment=False)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/listening/<int:lid>/audio")
def api_listening_audio(lid):
    if not 0 <= lid < len(LISTENING):
        abort(404)
    if not EDGE_TTS_OK:
        abort(503)
    script = LISTENING[lid]["script"]
    cache_key = hashlib.sha256((script + "_v3paused").encode()).hexdigest()[:16]
    final_path = os.path.join(TTS_DIR, f"script_{cache_key}.mp3")
    if os.path.exists(final_path):
        with TTS_LOCK:
            TTS_FILES[final_path] = time.time()
        resp = send_file(final_path, mimetype="audio/mpeg", as_attachment=False)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    chunks = parse_script(script)
    part_paths = []
    base = f"seg_{cache_key}_{int(time.time()*1000)}"
    for i, ch in enumerate(chunks):
        seg_path = os.path.join(TTS_DIR, f"{base}_{i}.mp3")
        if ch["type"] == "silence":
            if _make_silent_mp3(ch["duration"], seg_path):
                part_paths.append(seg_path)
        else:
            try:
                _edge_tts_save(ch["text"], ch["voice"], seg_path, rate="-12%")
                if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                    part_paths.append(seg_path)
            except Exception:
                pass
    if not part_paths:
        abort(503)
    if not _concat_mp3s(part_paths, final_path):
        abort(503)
    with TTS_LOCK:
        TTS_FILES[final_path] = time.time()
    resp = send_file(final_path, mimetype="audio/mpeg", as_attachment=False)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ============================================================
#  AI (OpenRouter)
# ============================================================
@app.post("/api/ai/ask")
def api_ai_ask():
    if not OPENROUTER_API_KEY or "sk-or-v1-" not in OPENROUTER_API_KEY or "xxxx" in OPENROUTER_API_KEY:
        return jsonify(error="no_key", message="لم يتم إعداد مفتاح OpenRouter بشكل صحيح"), 503

    d = request.get_json(force=True) or {}
    question = str(d.get("question", "")).strip()[:500]
    card_title = str(d.get("card_title", "")).strip()[:200]
    card_body = str(d.get("card_body", "")).strip()[:4000]
    card_kind = str(d.get("card_kind", "")).strip()[:30]
    rule_title = str(d.get("rule_title", "")).strip()[:200]
    user_name = str(d.get("user_name", "")).strip()[:40]

    if not question:
        return jsonify(error="empty_question"), 400

    system_prompt = globals().get("AI_SYSTEM_PROMPT", "أنت مساعد ذكي ومفيد.")
    user_prompt = build_ai_prompt(user_name, rule_title, card_title, card_body, card_kind, question)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY.strip()}"
    }

    free_models = []
    try:
        req_m = urllib.request.Request("https://openrouter.ai/api/v1/models")
        with urllib.request.urlopen(req_m, timeout=5) as resp_m:
            data_m = json.loads(resp_m.read().decode("utf-8"))
            free_models = [m["id"] for m in data_m.get("data", []) if m.get("id", "").endswith(":free")]
    except Exception as e:
        print(f">>> [OPENROUTER FETCH MODELS WARNING]: {e}")

    if not free_models:
        free_models = [
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.1-8b-instruct:free",
            "qwen/qwen-2.5-coder-32b-instruct:free"
        ]

    url = "https://openrouter.ai/api/v1/chat/completions"

    for model_name in free_models[:5]:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.6,
            "max_tokens": 1000
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if text:
                print(f">>> [OPENROUTER SUCCESS]: تم التوليد بنجاح عبر ({model_name})")
                return jsonify(answer=text)

        except Exception as e:
            print(f">>> [OPENROUTER TRY FAILED FOR {model_name}]: {e}")
            continue

    return jsonify(error="no_response", message="لم يتم الحصول على رد من الموديلات المتاحة"), 502


# ============================================================
#  API
# ============================================================
@app.get("/api/state")
def api_state():
    with db() as c:
        u = c.execute("SELECT * FROM user").fetchone()
        v = c.execute("SELECT COUNT(*) FROM vault WHERE mastered=0").fetchone()[0]
        return jsonify(user=dict(u) if u else None,
                       days=list(get_status(c).values()), vault=v)

@app.post("/api/onboard")
def api_onboard():
    d = request.get_json(force=True)
    name = str(d.get("name", "")).strip()[:40]
    target = int(d.get("target", 0) or 0)
    if not name or not 40 <= target <= 100:
        return jsonify(error="invalid"), 400
    with db() as c:
        c.execute("INSERT OR REPLACE INTO user(id,name,target,xp,level) VALUES(1,?,?,0,1)",
                  (name, target))
    return jsonify(ok=True)

@app.get("/api/day/<int:n>")
def api_day(n):
    if n not in DAYS: abort(404)
    with db() as c:
        st = get_status(c)[n]
        if not st["unlocked"]: return jsonify(error="locked"), 403
        rules = []
        for r_idx in DAYS[n]:
            rule = RULES[r_idx]
            first_q = next((i for i, q in enumerate(QUESTIONS) if q[0]==r_idx), None)
            done = bool(c.execute("SELECT 1 FROM lessons WHERE rid=?", (r_idx,)).fetchone())
            data = dict(rule)
            data["id"] = r_idx
            data["done"] = done
            data["test"] = pub_q(first_q) if first_q is not None else None
            data["colors"] = RULE_COLORS[r_idx]
            rules.append(data)
        reading = [pub_reading(READING[i]) for i in READING_DAYS.get(n, [])]
        listening = [pub_listening(LISTENING[i]) for i in LISTENING_DAYS.get(n, [])]
        return jsonify(rules=rules, reading=reading, listening=listening,
                       drill_passed=st["drill"], vault_open=st["vault"],
                       reading_of=st["reading_of"], listening_of=st["listening_of"])

@app.post("/api/lesson/<int:rid>")
def api_lesson_done(rid):
    if not 0 <= rid < len(RULES): abort(404)
    with db() as c:
        n = next((d for d, rs in DAYS.items() if rid in rs), None)
        if n is None or not get_status(c)[n]["unlocked"]:
            return jsonify(error="locked"), 403
        c.execute("INSERT OR IGNORE INTO lessons(rid) VALUES(?)", (rid,))
        add_xp(c, 20)
    return jsonify(ok=True)

@app.post("/api/lesson/<int:rid>/step/<int:si>")
def api_step_done(rid, si):
    if not 0 <= rid < len(RULES): abort(404)
    with db() as c:
        c.execute("INSERT OR IGNORE INTO step_progress(rid, step_index) VALUES(?,?)",
                  (rid, si))
        add_xp(c, 2)
    return jsonify(ok=True)

@app.get("/api/drill/<int:n>")
def api_drill(n):
    if n not in DAYS: abort(404)
    with db() as c:
        st = get_status(c)[n]
        if not st["unlocked"] or st["lessons"] != st["of"]:
            return jsonify(error="locked"), 403
        ids = drill_ids(c, n)
        c.execute("DELETE FROM attempts WHERE day=?", (n,))
        c.executemany("INSERT INTO attempts(day,qid,ok) VALUES(?,?,0)",
                      [(n, i) for i in ids])
        return jsonify(qs=[pub_q(i) for i in ids], thr=get_threshold(c, n))

@app.post("/api/drill/<int:n>/finish")
def api_drill_finish(n):
    if n not in DAYS: abort(404)
    with db() as c:
        t, k = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok),0) FROM attempts WHERE day=?",
            (n,)).fetchone()
        pct = round(100 * k / t) if t else 0
        thr = get_threshold(c, n)
        ok = int(pct >= thr)
        c.execute("INSERT OR REPLACE INTO drills(day,score,passed) VALUES(?,?,?)",
                  (n, pct, ok))
        if ok: add_xp(c, 50)
    return jsonify(pct=pct, passed=bool(ok), thr=thr)

@app.post("/api/answer")
def api_answer():
    d = request.get_json(force=True)
    qid, choice, ctx, day_ = int(d["qid"]), int(d["choice"]), d.get("ctx"), int(d.get("day") or 0)
    if not 0 <= qid < len(QUESTIONS): abort(404)
    q = QUESTIONS[qid]
    ok = int(choice == q[3])
    mastered = 0; xp_gain = 0
    with db() as c:
        if ctx == "drill":
            c.execute("UPDATE attempts SET ok=? WHERE day=? AND qid=?",
                      (ok, day_, qid))
        row = c.execute("SELECT * FROM vault WHERE qid=?", (qid,)).fetchone()
        if not ok:
            c.execute("""INSERT INTO vault(qid,day) VALUES(?,?)
                ON CONFLICT(qid) DO UPDATE SET wrong=wrong+1, streak=0, mastered=0,
                day=CASE WHEN ?>0 THEN ? ELSE day END""", (qid, day_, day_, day_))
        elif row and ctx == "vault":
            st = row["streak"] + 1
            mastered = int(st >= 2)
            c.execute("UPDATE vault SET streak=?, mastered=? WHERE qid=?",
                      (st, mastered, qid))
            if mastered: xp_gain = 15
        if ok: xp_gain += 5
        if xp_gain: add_xp(c, xp_gain)
    return jsonify(ok=bool(ok), ans=q[3], expl=q[4], tr=q[6],
                   full_sentence=q[5], mastered=bool(mastered), xp_gain=xp_gain)

@app.get("/api/vault")
def api_vault():
    n = int(request.args.get("day", 0) or 0)
    sql, args = "SELECT qid FROM vault WHERE mastered=0", ()
    if n:
        sql += " AND day=?"; args = (n,)
    with db() as c:
        ids = [r[0] for r in c.execute(sql + " ORDER BY streak, wrong DESC", args)]
    return jsonify(qs=[pub_q(i) for i in ids])

@app.get("/api/progress")
def api_progress():
    with db() as c:
        out = []
        for r_idx, rule in enumerate(RULES):
            total = sum(1 for q in QUESTIONS if q[0] == r_idx)
            if total == 0:
                out.append(dict(rid=r_idx, title=rule["title"], total=0, pct=100,
                                colors=RULE_COLORS[r_idx]))
                continue
            correct = 0
            for qi, q in enumerate(QUESTIONS):
                if q[0] != r_idx: continue
                row = c.execute(
                    "SELECT ok FROM attempts WHERE qid=? ORDER BY day DESC LIMIT 1",
                    (qi,)).fetchone()
                if row and row["ok"]: correct += 1
            pct = round(100 * correct / total) if total else 100
            out.append(dict(rid=r_idx, title=rule["title"], total=total, pct=pct,
                            colors=RULE_COLORS[r_idx]))
        return jsonify(rules=out)

@app.get("/api/reading/<int:rid>")
def api_reading(rid):
    if not 0 <= rid < len(READING): abort(404)
    return jsonify(pub_reading(READING[rid]))

@app.post("/api/reading/<int:rid>/answer")
def api_reading_answer(rid):
    if not 0 <= rid < len(READING): abort(404)
    d = request.get_json(force=True)
    qid = int(d["qid"]); choice = int(d["choice"])
    passage = READING[rid]
    if not 0 <= qid < len(passage["questions"]): abort(404)
    q = passage["questions"][qid]
    ok = int(choice == q["ans"])
    with db() as c:
        c.execute("INSERT OR REPLACE INTO reading_attempts(rid,qid,ok) VALUES(?,?,?)",
                  (rid, qid, ok))
        if ok: add_xp(c, 4)
    return jsonify(ok=bool(ok), ans=q["ans"], expl=q["expl"], tr=q["tr"])

@app.post("/api/reading/<int:rid>/finish")
def api_reading_finish(rid):
    if not 0 <= rid < len(READING): abort(404)
    with db() as c:
        t, k = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok),0) FROM reading_attempts WHERE rid=?",
            (rid,)).fetchone()
        pct = round(100 * k / t) if t else 0
        c.execute("INSERT OR REPLACE INTO reading_progress(rid) VALUES(?)", (rid,))
        add_xp(c, 30)
    return jsonify(pct=pct)

@app.get("/api/listening/<int:lid>")
def api_listening(lid):
    if not 0 <= lid < len(LISTENING): abort(404)
    return jsonify(pub_listening(LISTENING[lid]))

@app.post("/api/listening/<int:lid>/answer")
def api_listening_answer(lid):
    if not 0 <= lid < len(LISTENING): abort(404)
    d = request.get_json(force=True)
    qid = int(d["qid"]); choice = int(d["choice"])
    script = LISTENING[lid]
    if not 0 <= qid < len(script["questions"]): abort(404)
    q = script["questions"][qid]
    ok = int(choice == q["ans"])
    with db() as c:
        c.execute("INSERT OR REPLACE INTO listening_attempts(lid,qid,ok) VALUES(?,?,?)",
                  (lid, qid, ok))
        if ok: add_xp(c, 4)
    return jsonify(ok=bool(ok), ans=q["ans"], expl=q["expl"], tr=q["tr"])

@app.post("/api/listening/<int:lid>/finish")
def api_listening_finish(lid):
    if not 0 <= lid < len(LISTENING): abort(404)
    with db() as c:
        t, k = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok),0) FROM listening_attempts WHERE lid=?",
            (lid,)).fetchone()
        pct = round(100 * k / t) if t else 0
        c.execute("INSERT OR REPLACE INTO listening_progress(lid) VALUES(?)", (lid,))
        add_xp(c, 30)
    return jsonify(pct=pct)


# ============================================================
#  PAGE — Pearl Reef (Stable Single-Card)
# ============================================================
PAGE = r"""{% raw %}<!doctype html>
<html lang="ar" dir="rtl" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>STEP Coach — Pearl Reef</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Alexandria:wght@400;700;800&family=Inter:wght@500;700;800&display=swap">
<style>
/* ============================================================
   STEP Glass — Pearl Reef (Stable, Single-Card)
   ============================================================ */
:root,[data-theme=dark]{
  --bg:#081a1e; --ink:#e3eeee; --mut:#90b0b3;
  --g:rgba(255,255,255,.085); --gw:rgba(255,255,255,.045);
  --gb:rgba(255,255,255,.2); --lit:rgba(255,255,255,.38);
  --ac:#5cc9c4; --ac2:#e49a82; --on:#06282b; --cau:.15;
  --ok:#5cc9c4; --bad:#e49a82;
  --shadow:0 34px 50px -34px #000a;
}
[data-theme=light]{
  --bg:#e9f1f0; --ink:#12333a; --mut:#51737a;
  --g:rgba(255,255,255,.78); --gw:rgba(255,255,255,.4);
  --gb:rgba(255,255,255,.98); --lit:#fff;
  --ac:#2a7f8c; --ac2:#c0583a; --on:#fff; --cau:.28;
  --ok:#2a7f8c; --bad:#c0583a;
  --shadow:0 34px 50px -34px rgba(20,40,50,.18);
}
*,*::before,*::after{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0;min-height:100vh;background:var(--bg);color:var(--ink);
  font:400 15.5px/1.9 'Alexandria',system-ui,-apple-system,sans-serif;
  overflow-x:hidden;-webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
}

/* ---------- 1) المشهد ---------- */
.scene{position:fixed;inset:0;z-index:-1;overflow:hidden;
  background:
    radial-gradient(60vmax 50vmax at 88% -12%,color-mix(in srgb,var(--ac) 15%,transparent),transparent 70%),
    radial-gradient(50vmax 40vmax at -5% 112%,color-mix(in srgb,var(--ac2) 8%,transparent),transparent 70%)}
.scene svg{position:absolute;inset:0;width:100%;height:100%;mix-blend-mode:soft-light;opacity:var(--cau)}
.pb{position:absolute;width:var(--s);height:var(--s);left:var(--x);top:var(--y);border-radius:50%;opacity:.28;
  background:
    radial-gradient(circle at 30% 24%,#fff 0 6%,#fff8 10%,transparent 38%),
    radial-gradient(circle at 70% 80%,color-mix(in srgb,var(--ac) 50%,transparent),transparent 62%);
  box-shadow:inset -6px -10px 22px #fff5,inset 6px 8px 18px #fff7;
  animation:bob 9s ease-in-out infinite}
@keyframes bob{50%{transform:translateY(-18px)}}

/* ---------- 2) شريط النظام ---------- */
.mb{position:fixed;top:0;left:0;right:0;z-index:40;display:flex;align-items:center;gap:16px;
  padding:8px 22px;font-size:.82rem;
  background:var(--g);
  -webkit-backdrop-filter:blur(22px) saturate(1.6);backdrop-filter:blur(22px) saturate(1.6);
  border-bottom:1px solid var(--gb)}
.mb b{font-weight:800}
.mb .sb{color:var(--mut)}
.mb em{margin-inline-start:auto;font-style:normal;color:var(--mut);font-size:.78rem}
.t{font:inherit;font-size:.78rem;color:var(--ink);background:var(--gw);border:1px solid var(--gb);
  border-radius:99px;padding:3px 13px;cursor:pointer;transition:background .2s;font-family:inherit}
.t:hover{background:color-mix(in srgb,var(--ac) 18%,transparent)}
.t:active{transform:translateY(1px)}

/* ---------- 3) النافذة Split View ---------- */
.win{max-width:1020px;margin:76px auto 130px;display:grid;grid-template-columns:230px 1fr;
  border-radius:44px;
  background:var(--gw);
  -webkit-backdrop-filter:blur(34px) saturate(1.5);backdrop-filter:blur(34px) saturate(1.5);
  border:1px solid var(--gb);
  box-shadow:inset 0 2px 1px var(--lit),var(--shadow);
  overflow:hidden;min-height:calc(100vh - 200px)}
.win.solo{grid-template-columns:1fr}
.win.solo aside{display:none}
.win aside{padding:26px 14px;
  background:color-mix(in srgb,var(--ink) 4%,transparent);
  border-inline-end:1px solid var(--gb);
  overflow-y:auto;max-height:calc(100vh - 200px)}
.win aside .k{padding:0 12px;display:block;margin-bottom:6px}
.unit{display:flex;align-items:center;gap:10px;padding:9px 12px;margin-top:4px;border-radius:16px;
  color:var(--mut);cursor:pointer;transition:background .25s,color .25s;font-size:.9rem;
  background:none;border:0;font-family:inherit;text-align:start;width:100%}
.unit i{width:8px;height:8px;border-radius:50%;background:currentColor;opacity:.4;flex-shrink:0}
.unit.done i{background:var(--ac);opacity:1}
.unit.locked{opacity:.55;cursor:default}
.unit.on{background:var(--g);color:var(--ink);font-weight:700;
  box-shadow:inset 0 1px 1px var(--lit),0 8px 20px -10px #0006}
.unit.on i{background:var(--ac);opacity:1;box-shadow:0 0 6px color-mix(in srgb,var(--ac) 60%,transparent)}
.main{padding:28px;min-width:0}
.top{display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}
.top h1{margin:0;font-size:clamp(24px,4.5vw,38px);line-height:1.2;font-weight:800}
.top h1.sm{font-size:clamp(18px,3vw,24px)}
.k{font-size:.76rem;letter-spacing:.06em;color:var(--mut);font-weight:700;text-transform:uppercase}
.ring{width:84px;height:84px;flex:none}
.ring circle{fill:none;stroke-width:7;stroke-linecap:round}
.ring .t0{stroke:color-mix(in srgb,var(--ink) 12%,transparent)}
.ring .t1{stroke:var(--ac);stroke-dasharray:226;stroke-dashoffset:85;transform:rotate(-90deg);
  transform-origin:50% 50%;filter:drop-shadow(0 0 3px color-mix(in srgb,var(--ac) 60%,transparent));
  transition:stroke-dashoffset 1.2s ease}
.ring text{font:800 17px 'Alexandria';fill:var(--ink)}

/* ---------- 4) البطاقة ---------- */
.card{position:relative;padding:26px;border-radius:36px;
  background:var(--g);border:1px solid var(--gb);
  box-shadow:inset 0 2px 1px var(--lit),inset 0 -12px 22px -14px color-mix(in srgb,var(--ac) 45%,transparent),
    var(--shadow)}
.card::before{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:radial-gradient(360px circle at var(--mx,50%) var(--my,0%),color-mix(in srgb,#fff 20%,transparent),transparent 60%)}
.card::after{content:"";position:absolute;inset:0;border-radius:inherit;padding:1.5px;pointer-events:none;
  background:conic-gradient(from 210deg,#ffd9c777,#bff5ee77,#fff3cf77,#ffd9c777);
  -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;
  mask:linear-gradient(#000 0 0) content-box exclude,linear-gradient(#000 0 0);
  mask-composite:exclude}
.card h2{margin:2px 0 8px;font-size:1.3rem;line-height:1.35;font-weight:800}
.card h2.mid{font-size:1.1rem;margin-bottom:12px}

/* ---------- 5) قائمة البطاقات ---------- */
.col{display:grid;gap:26px;margin-top:22px}

/* ---------- 6) محتوى البطاقات ---------- */
.en{direction:ltr;text-align:left;font-size:1.6rem;font-weight:700;margin:4px 0 0;
  font-family:'Inter','Alexandria',system-ui,sans-serif;word-break:break-word}
.row{display:grid;grid-template-columns:1fr 1fr 1.5fr;gap:8px;padding:9px 14px;border-radius:18px;
  transition:background .2s;font-size:.92rem;align-items:center}
.row.c2{grid-template-columns:1fr 1.4fr}
.row.h{color:var(--mut);font-size:.8rem;font-weight:700}
.row:hover:not(.h){background:color-mix(in srgb,var(--ink) 7%,transparent)}
.row b{direction:ltr;text-align:left;color:var(--ac);
  font-family:'Inter','Alexandria',monospace;font-weight:800}
.row span{color:var(--ink)}
.row span:nth-child(2){color:var(--mut)}
.bar{height:10px;border-radius:9px;background:color-mix(in srgb,var(--ink) 12%,transparent);overflow:hidden}
.bar i{display:block;height:100%;border-radius:9px;background:var(--ac);
  box-shadow:0 0 8px color-mix(in srgb,var(--ac) 60%,transparent);transition:width .6s ease}
.btn{font:inherit;font-weight:700;cursor:pointer;color:var(--on);background:var(--ac);
  border:0;border-radius:99px;padding:12px 32px;
  box-shadow:inset 0 2px 1px #fff7,0 14px 26px -14px var(--ac);
  transition:transform .18s, box-shadow .18s;
  font-family:inherit;-webkit-tap-highlight-color:transparent}
.btn:hover{transform:translateY(-1px)}
.btn:active{transform:translateY(1px);box-shadow:inset 0 2px 1px #fff5,0 6px 14px -10px var(--ac)}
.btn:disabled{opacity:.5;cursor:default;transform:none}
.btn.gh{background:var(--gw);color:var(--ink);border:1px solid var(--gb);
  box-shadow:inset 0 1px 1px var(--lit)}
.btn.gh:hover{background:color-mix(in srgb,var(--ac) 12%,transparent)}
.btn.gh:active{transform:translateY(1px)}
.btn-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
.btn-row .btn{flex:1;min-width:140px}
.rv{display:grid;grid-template-rows:0fr;transition:grid-template-rows .5s cubic-bezier(.2,.9,.2,1)}
.rv>div{overflow:hidden}
.rv.s{grid-template-rows:1fr}
.chip{display:inline-block;margin:12px 6px 0 0;padding:3px 16px;border-radius:99px;font-weight:700;
  direction:ltr;background:color-mix(in srgb,var(--ink) 8%,transparent);border:1px solid var(--gb);
  font-family:'Inter','Alexandria',monospace;font-size:.88rem}
.chip::before{content:"◆ ";color:var(--ac)}
.trap{margin-top:14px;padding:12px 16px;border-radius:20px;
  background:color-mix(in srgb,var(--ac2) 15%,transparent);
  border:1px solid color-mix(in srgb,var(--ac2) 50%,transparent);font-size:.92rem;line-height:1.75}
.trap b{color:var(--ac2);display:block;margin-bottom:2px}
.gold{margin-top:10px;padding:12px 16px;border-radius:20px;
  background:color-mix(in srgb,var(--ac) 13%,transparent);
  border:1px solid color-mix(in srgb,var(--ac) 45%,transparent);font-size:.92rem;line-height:1.75}
.gold b{color:var(--ac);display:block;margin-bottom:2px}
.q{display:inline-block;padding:6px 16px;border-radius:99px;
  background:color-mix(in srgb,var(--ink) 9%,transparent);margin-bottom:10px;font-size:.92rem;
  border:1px solid var(--gb)}
.q::before{content:"؟ ";color:var(--ac);font-weight:800}

/* ---------- 7) السائل (Liquid) ---------- */
.lq{-webkit-backdrop-filter:blur(3px) saturate(1.9) brightness(1.06);
  backdrop-filter:blur(3px) saturate(1.9) brightness(1.06);
  backdrop-filter:url(#lg) blur(3px) saturate(1.9) brightness(1.06);
  box-shadow:inset 0 0 0 1px var(--gb),inset 3px 4px 8px -3px var(--lit),
    inset -4px -5px 10px -4px #0005,0 24px 40px -22px #000a}
.nav{position:fixed;z-index:30;display:flex;gap:2px;padding:6px;bottom:18px;left:50%;
  transform:translateX(-50%);border-radius:99px;background:var(--g);border:1px solid var(--gb)}
.nav a{position:relative;z-index:1;cursor:pointer;padding:10px 20px;border-radius:99px;
  color:var(--mut);font-size:.92rem;white-space:nowrap;
  transition:color .3s;font-family:inherit;-webkit-tap-highlight-color:transparent}
.nav a.on{color:var(--ink);font-weight:700}
.ind{position:absolute;z-index:0;border-radius:99px;
  background:color-mix(in srgb,var(--ac) 22%,transparent);
  box-shadow:inset 0 1px 1px var(--lit);
  transition:left .4s cubic-bezier(.3,1.4,.5,1),top .4s cubic-bezier(.3,1.4,.5,1),
             width .4s cubic-bezier(.3,1.4,.5,1),height .4s cubic-bezier(.3,1.4,.5,1)}
.lens{position:fixed;z-index:30;left:18px;bottom:22px;width:62px;height:62px;border-radius:50%;
  border:1px solid var(--gb);background:var(--g);color:var(--ink);
  font:800 .95rem 'Alexandria';cursor:pointer;
  display:grid;place-items:center;-webkit-tap-highlight-color:transparent;
  transition:background .2s, transform .15s}
.lens:hover{background:color-mix(in srgb,var(--ac) 20%,var(--g))}
.lens:active{transform:translateY(2px)}

/* ---------- مكونات إضافية ---------- */
.spk{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:50%;
  border:1px solid var(--gb);background:var(--g);color:var(--ac);cursor:pointer;
  vertical-align:middle;margin-inline-start:8px;font:700 .8rem 'Alexandria';
  transition:background .15s, transform .12s;-webkit-tap-highlight-color:transparent}
.spk:hover{background:color-mix(in srgb,var(--ac) 16%,transparent)}
.spk:active{transform:scale(.94)}
.spk.playing{background:var(--ac);color:var(--on)}

.text-block{white-space:pre-line;font-size:.95rem;line-height:2;color:var(--ink);
  text-align:right;direction:rtl;padding:4px 0}

/* word_analysis */
.wa-sentence{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;padding:18px 12px;
  background:color-mix(in srgb,var(--ink) 5%,transparent);border-radius:18px;
  border:1px solid var(--gb);margin:12px 0;direction:ltr}
.wa-cell{display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 8px;
  border-radius:12px;background:var(--g);border:2px solid var(--wc,var(--ac));min-width:64px;
  cursor:pointer;transition:transform .15s,box-shadow .15s;
  box-shadow:0 3px 8px -4px rgba(0,0,0,.15)}
.wa-cell:hover{transform:translateY(-2px)}
.wa-cell.active{transform:scale(1.05);
  box-shadow:0 0 0 3px var(--g),0 0 0 5px var(--wc,var(--ac))}
.wa-en{font-family:'Inter',monospace;font-size:16px;font-weight:800;color:var(--wc,var(--ac));direction:ltr}
.wa-arrow{font-size:10px;color:var(--wc,var(--ac));opacity:.5;line-height:1}
.wa-ar{font-family:'Alexandria';font-size:13px;font-weight:700;color:var(--ink)}
.wa-role{font-family:'Alexandria';font-size:10px;font-weight:700;color:var(--on);
  background:var(--wc,var(--ac));padding:1px 8px;border-radius:99px;margin-top:2px}
.wa-detail{margin-top:10px;min-height:70px}
.wa-detail-inner{background:color-mix(in srgb,var(--ink) 5%,transparent);
  border:2px solid var(--wd-color,var(--ac));border-radius:18px;padding:14px 16px;
  animation:detailIn .3s ease}
@keyframes detailIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
.wa-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.wa-word-big{font-family:'Inter',monospace;direction:ltr;font-size:22px;font-weight:800;
  color:var(--wd-color,var(--ac))}
.wa-type-tag{display:inline-block;font-size:11px;font-weight:700;padding:3px 10px;border-radius:99px;
  background:var(--wd-color,var(--ac));color:var(--on);direction:rtl}
.wa-detail-row{font-size:.9rem;color:var(--ink);margin:5px 0;line-height:1.7}
.wa-detail-row b{color:var(--wd-color,var(--ac))}
.wa-hint{text-align:center;font-size:.78rem;color:var(--mut);margin:8px 0;font-weight:600}
.wa-hint b{color:var(--ac)}
.wa-legend{display:flex;flex-wrap:wrap;gap:5px;justify-content:center;margin:8px 0;padding:6px;
  background:color-mix(in srgb,var(--ink) 4%,transparent);border-radius:12px;border:1px solid var(--gb)}
.wa-legend-item{display:flex;align-items:center;gap:3px;font-size:.7rem;font-weight:700;color:var(--mut)}
.wa-legend-dot{width:8px;height:8px;border-radius:50%}

.formula{font-family:'Inter',monospace;direction:ltr;text-align:left;
  background:color-mix(in srgb,var(--ac) 12%,transparent);
  border:1.5px solid color-mix(in srgb,var(--ac) 45%,transparent);
  border-radius:14px;padding:10px 14px;font-weight:700;color:var(--ink);
  font-size:.92rem;margin:6px 0;word-break:break-word}
.f-row{padding:11px 0;border-bottom:1px solid var(--gb)}
.f-row:last-child{border-bottom:0}
.f-label{font-weight:800;font-size:.88rem;margin-bottom:5px;color:var(--ink)}
.f-en{font-family:'Inter',monospace;direction:ltr;text-align:left;color:var(--mut);margin-top:5px;font-size:.82rem}

/* options */
.opt-btn{display:flex;align-items:center;gap:10px;width:100%;text-align:left;
  padding:13px 15px;margin:7px 0;border-radius:16px;background:var(--gw);
  border:1.5px solid var(--gb);font-family:'Inter','Alexandria',sans-serif;
  font-size:.92rem;font-weight:600;color:var(--ink);cursor:pointer;
  transition:border-color .15s, background .15s, transform .15s;
  direction:ltr;min-height:48px;line-height:1.4;-webkit-tap-highlight-color:transparent}
.opt-btn:hover:not(:disabled){border-color:var(--ac);
  background:color-mix(in srgb,var(--ac) 10%,transparent)}
.opt-btn:active:not(:disabled){transform:translateY(1px)}
.opt-btn:disabled{cursor:default}
.opt-letter{width:28px;height:28px;border-radius:9px;
  background:color-mix(in srgb,var(--ink) 8%,transparent);color:var(--ink);
  font-weight:800;display:grid;place-items:center;flex-shrink:0;
  font-family:'Inter';font-size:12px}
.opt-btn.correct{background:color-mix(in srgb,var(--ac) 18%,transparent);border-color:var(--ac)}
.opt-btn.correct .opt-letter{background:var(--ac);color:var(--on)}
.opt-btn.wrong{background:color-mix(in srgb,var(--ac2) 18%,transparent);border-color:var(--ac2)}
.opt-btn.wrong .opt-letter{background:var(--ac2);color:var(--on)}

/* feedback */
.fb{margin-top:14px;padding:14px;border-radius:18px;animation:slideUp .3s ease both}
@keyframes slideUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.fb-ok{background:color-mix(in srgb,var(--ac) 15%,transparent);
  border:1.5px solid color-mix(in srgb,var(--ac) 55%,transparent)}
.fb-bad{background:color-mix(in srgb,var(--ac2) 15%,transparent);
  border:1.5px solid color-mix(in srgb,var(--ac2) 55%,transparent)}
.fb-title{font-weight:800;font-size:.92rem;margin-bottom:6px;color:var(--ink)}
.fb-body{font-size:.88rem;line-height:1.75;color:var(--ink)}
.fb-sent{font-family:'Inter',monospace;direction:ltr;text-align:left;
  background:color-mix(in srgb,var(--ink) 5%,transparent);
  padding:8px 12px;border-radius:12px;margin:8px 0;font-size:.88rem;
  font-weight:700;color:var(--ink);word-break:break-word}

/* passage */
.passage{font-family:'Inter','Alexandria',system-ui;direction:ltr;text-align:left;
  font-size:.98rem;line-height:1.9;color:var(--ink);
  background:color-mix(in srgb,var(--ink) 4%,transparent);
  border-radius:18px;padding:16px 18px;border:1px solid var(--gb);
  white-space:pre-line;max-height:48vh;overflow-y:auto;max-width:68ch}

/* audio */
.audio-panel{background:color-mix(in srgb,var(--ac) 10%,transparent);
  border:1.5px solid color-mix(in srgb,var(--ac) 40%,transparent);
  border-radius:22px;padding:16px;margin:12px 0;text-align:center}
.audio-panel .play-orb{width:68px;height:68px;margin:6px auto 10px;border-radius:50%;
  background:var(--ac);color:var(--on);display:grid;place-items:center;cursor:pointer;
  box-shadow:inset 0 2px 1px #fff7,0 10px 24px -10px var(--ac);
  transition:transform .15s;font-size:26px;border:0;font-family:inherit;
  -webkit-tap-highlight-color:transparent}
.audio-panel .play-orb:hover{transform:scale(1.04)}
.audio-panel .play-orb:active{transform:scale(.96)}
.audio-panel .play-orb.playing{animation:playPulse 1.5s infinite}
@keyframes playPulse{0%,100%{box-shadow:inset 0 2px 1px #fff7,0 10px 24px -10px var(--ac),0 0 0 0 var(--ac)}
  50%{box-shadow:inset 0 2px 1px #fff7,0 10px 24px -10px var(--ac),0 0 0 14px transparent}}
.audio-title{font-weight:800;font-size:.92rem;color:var(--ink)}
.audio-sub{font-size:.76rem;color:var(--mut);margin-top:2px}

/* script */
.script-lines{background:color-mix(in srgb,var(--ink) 6%,transparent);
  border-radius:16px;padding:12px 14px;max-height:36vh;overflow-y:auto;
  direction:ltr;text-align:left;font-family:'Inter',monospace;font-size:.82rem;
  line-height:1.75;color:var(--ink);border:1px solid var(--gb)}
.script-line{margin:4px 0;display:block}
.script-line.directive{color:var(--ac);font-weight:800}
.script-line.pause{color:var(--mut);font-weight:700;font-style:italic}
.script-line.speech{color:var(--ink)}

/* timer */
.timer-track{height:4px;background:color-mix(in srgb,var(--ink) 12%,transparent);
  border-radius:99px;overflow:hidden;margin-bottom:10px}
.timer-fill{height:100%;background:var(--ac);border-radius:99px;transition:width 1s linear}

/* progress bars */
.pbar{display:flex;align-items:center;gap:9px;margin:10px 0}
.pbar-label{font-size:.82rem;font-weight:700;min-width:120px;color:var(--ink);line-height:1.35}
.pbar-track{flex:1;height:9px;border-radius:99px;
  background:color-mix(in srgb,var(--ink) 12%,transparent);overflow:hidden}
.pbar-fill{height:100%;border-radius:99px;background:var(--ac);transition:width .6s ease;
  box-shadow:0 0 8px color-mix(in srgb,var(--ac) 60%,transparent)}
.pbar-pct{font-size:.76rem;font-weight:800;min-width:40px;text-align:left;color:var(--mut)}

/* journey */
.day-row{display:flex;align-items:center;gap:12px;padding:11px 0;cursor:pointer;
  transition:transform .15s;position:relative;background:none;border:0;
  font-family:inherit;width:100%;text-align:start;color:inherit;
  -webkit-tap-highlight-color:transparent}
.day-row.locked{cursor:default;opacity:.55}
.day-row:hover:not(.locked){transform:translateX(-3px)}
.day-row:active:not(.locked){transform:translateX(0)}
.day-body{flex:1;min-width:0}
.day-title{font-weight:800;font-size:.92rem;line-height:1.4;color:var(--ink)}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.chips .chip{margin:0;font-size:.72rem;padding:2px 10px;font-weight:700}
.orb{width:38px;height:38px;border-radius:50%;color:var(--on);font-weight:800;
  display:grid;place-items:center;flex-shrink:0;font-size:14px;background:var(--ac);
  box-shadow:inset 0 -3px 6px rgba(0,0,0,.15),0 6px 14px -6px var(--ac)}
.orb-ok{background:var(--ac)}
.orb-lock{background:color-mix(in srgb,var(--ink) 20%,transparent);color:var(--mut);
  box-shadow:none;opacity:.7}

/* plan grid */
.plan-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.plan-item{display:flex;align-items:center;gap:8px;
  background:color-mix(in srgb,var(--ink) 5%,transparent);
  border:1px solid var(--gb);border-radius:14px;padding:9px 12px}
.plan-lbl{flex:1;font-weight:700;font-size:.82rem}
.plan-time{font-size:.72rem;font-weight:800;color:var(--ac)}

/* steps */
.steps{display:flex;gap:5px;margin-bottom:14px;justify-content:center;flex-wrap:wrap}
.step-dot{width:8px;height:8px;border-radius:99px;
  background:color-mix(in srgb,var(--ink) 15%,transparent);transition:all .25s}
.step-dot.active{background:var(--ac);width:22px;
  box-shadow:0 0 0 3px color-mix(in srgb,var(--ac) 22%,transparent)}
.step-dot.done{background:var(--ac)}

/* app fade — بدون transform */
#app > *{animation:appFade .2s ease both}
@keyframes appFade{from{opacity:.55}to{opacity:1}}

/* empty */
.vault-empty{text-align:center;padding:34px 18px}
.vault-empty .big{font-size:48px;margin-bottom:8px;opacity:.7}

/* tag */
.tag{display:inline-block;font-size:.7rem;font-weight:700;padding:3px 11px;border-radius:99px;
  background:color-mix(in srgb,var(--ac) 15%,transparent);color:var(--ac);
  margin-bottom:8px;letter-spacing:.4px;text-transform:uppercase}
.tag-warn{background:color-mix(in srgb,var(--ac2) 15%,transparent);color:var(--ac2)}

/* toast */
#toastArea{position:fixed;bottom:110px;left:50%;transform:translateX(-50%);
  z-index:9997;display:flex;flex-direction:column;gap:8px;align-items:center;
  pointer-events:none;width:100%;max-width:420px;padding:0 16px}
.toast{padding:12px 22px;border-radius:18px;background:var(--g);
  -webkit-backdrop-filter:blur(18px) saturate(1.6);backdrop-filter:blur(18px) saturate(1.6);
  border:1px solid var(--gb);color:var(--ink);font-weight:700;font-size:.85rem;
  box-shadow:inset 0 2px 1px var(--lit),0 20px 40px -20px #000a;
  opacity:0;transform:translateY(20px);
  transition:opacity .3s ease, transform .3s cubic-bezier(.2,.9,.2,1);
  text-align:center;pointer-events:auto;max-width:100%}
.toast.show{opacity:1;transform:translateY(0)}
.toast::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;
  background:var(--ac);margin-inline-end:8px;vertical-align:middle;
  box-shadow:0 0 8px var(--ac)}
.toast.warn::before{background:var(--ac2);box-shadow:0 0 8px var(--ac2)}

/* confetti */
@keyframes confettiFall{0%{transform:translateY(-20px) rotate(0);opacity:1}
  100%{transform:translateY(100vh) rotate(720deg);opacity:0}}
.confetti-piece{position:fixed;width:8px;height:8px;border-radius:2px;pointer-events:none;
  z-index:9999;animation:confettiFall 2.8s ease-out forwards}

/* ---------- AI Chat ---------- */
.ai-chat-overlay{position:fixed;inset:0;z-index:9999;
  background:color-mix(in srgb,var(--bg) 60%,transparent);
  backdrop-filter:blur(10px) saturate(140%);
  -webkit-backdrop-filter:blur(10px) saturate(140%);
  display:flex;align-items:flex-end;justify-content:center;padding:14px;
  animation:aiFade .2s ease}
@media(min-width:640px){.ai-chat-overlay{align-items:center}}
@keyframes aiFade{from{opacity:0}to{opacity:1}}
.ai-chat-panel{width:100%;max-width:520px;max-height:85vh;display:flex;flex-direction:column;
  background:var(--g);border-radius:32px;overflow:hidden;
  -webkit-backdrop-filter:blur(22px) saturate(1.5);
  backdrop-filter:blur(22px) saturate(1.5);
  border:1px solid var(--gb);
  box-shadow:inset 0 2px 1px var(--lit),0 30px 60px -30px #000c;
  animation:aiSlideUp .28s cubic-bezier(.4,0,.2,1)}
@keyframes aiSlideUp{from{transform:translateY(22px);opacity:0}to{transform:translateY(0);opacity:1}}
.ai-chat-head{position:relative;display:flex;align-items:center;gap:12px;
  padding:14px 18px;color:var(--ink);border-bottom:1px solid var(--gb)}
.ai-chat-head::after{content:"";position:absolute;inset:0;
  background:linear-gradient(115deg,transparent 38%,color-mix(in srgb,var(--ac) 12%,transparent) 48%,transparent 58%);
  pointer-events:none}
.ai-chat-icon{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;
  background:var(--ac);color:var(--on);font-size:14px;font-weight:800;flex-shrink:0;
  box-shadow:inset 0 2px 1px #fff5;font-family:'Inter',sans-serif}
.ai-chat-titles{flex:1;min-width:0;position:relative;z-index:1}
.ai-chat-name{font-weight:800;font-size:.92rem;line-height:1.2}
.ai-chat-sub{font-size:.76rem;color:var(--mut);margin-top:3px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ai-chat-close{width:34px;height:34px;border-radius:10px;border:1px solid var(--gb);
  background:var(--gw);color:var(--ink);cursor:pointer;font-size:14px;
  display:grid;place-items:center;transition:background .15s;flex-shrink:0;
  position:relative;z-index:1;font-family:inherit}
.ai-chat-close:hover{background:color-mix(in srgb,var(--ac) 18%,transparent)}
.ai-chat-close:active{transform:translateY(1px)}
.ai-chat-messages{flex:1;min-height:200px;overflow-y:auto;padding:18px;
  display:flex;flex-direction:column;gap:12px}
.ai-chat-messages::-webkit-scrollbar{width:5px}
.ai-chat-messages::-webkit-scrollbar-thumb{background:var(--gb);border-radius:99px}
.ai-msg{display:flex;animation:aiMsgIn .25s ease}
@keyframes aiMsgIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.ai-msg-user{justify-content:flex-end}
.ai-msg-bot{justify-content:flex-start}
.ai-msg-body{max-width:88%;padding:12px 15px;border-radius:18px;
  font-size:.88rem;line-height:1.8;word-break:break-word;direction:rtl;text-align:right}
.ai-msg-user .ai-msg-body{background:var(--ac);color:var(--on);
  border-bottom-left-radius:6px;box-shadow:0 6px 14px -6px var(--ac);font-weight:600}
.ai-msg-bot .ai-msg-body{max-width:100%;background:var(--g);
  color:var(--ink);border:1px solid var(--gb);border-bottom-right-radius:6px;
  box-shadow:inset 0 2px 1px var(--lit),0 8px 22px -12px #0008;padding:14px}
.ai-msg-body p{margin:8px 0}
.ai-msg-body p:first-child{margin-top:0}
.ai-msg-body p:last-child{margin-bottom:0}
.ai-msg-body code{background:color-mix(in srgb,var(--ac) 15%,transparent);
  color:var(--ac);padding:2px 7px;border-radius:6px;
  font-family:'Inter',monospace;font-size:.82rem;direction:ltr;display:inline-block}
.ai-msg-user .ai-msg-body code{background:color-mix(in srgb,#000 20%,transparent);color:#fff}
.ai-msg-body strong{font-weight:800;color:var(--ac)}
.ai-msg-user .ai-msg-body strong{color:#fff}
/* AI 输出风格适配 (بدون .card) */
.ai-msg-body .k{display:block;margin:10px 0 4px;font-size:.72rem;
  color:var(--ac);font-weight:800;letter-spacing:.05em;text-transform:uppercase}
.ai-msg-body .k:first-child{margin-top:0}
.ai-msg-body .q{display:inline-block;padding:5px 14px;border-radius:99px;
  background:color-mix(in srgb,var(--ink) 9%,transparent);margin-bottom:10px;
  font-size:.85rem;border:1px solid var(--gb)}
.ai-msg-body .q::before{content:"؟ ";color:var(--ac);font-weight:800}
.ai-msg-body .en{font-size:1.15rem;margin:6px 0}
.ai-msg-body .formula{font-size:.85rem;margin:8px 0}
.ai-msg-body .row{grid-template-columns:1fr 1fr 1.2fr;font-size:.82rem;
  padding:7px 10px;gap:6px;border-radius:14px}
.ai-msg-body .row.c2{grid-template-columns:1fr 1.3fr}
.ai-msg-body .row b{font-size:.85rem}
.ai-msg-body .gold{margin-top:10px;padding:10px 12px;font-size:.82rem;border-radius:16px}
.ai-msg-body .gold b{font-size:.82rem}
.ai-msg-body .trap{margin-top:8px;padding:10px 12px;font-size:.82rem;border-radius:16px}
.ai-msg-body .trap b{font-size:.82rem}
.ai-msg-body .chip{margin-top:6px;font-size:.76rem;padding:2px 12px}
.ai-thinking span{opacity:.3;animation:aiDotPulse 1.2s infinite;font-weight:900;font-size:18px;line-height:1}
.ai-thinking span:nth-child(2){animation-delay:.2s}
.ai-thinking span:nth-child(3){animation-delay:.4s}
@keyframes aiDotPulse{0%,80%,100%{opacity:.3}40%{opacity:1}}
.ai-chat-input-row{display:flex;gap:8px;padding:12px 14px;border-top:1px solid var(--gb)}
.ai-chat-input{flex:1;padding:12px 15px;border:1.5px solid var(--gb);border-radius:14px;
  font-family:inherit;font-size:.88rem;outline:none;background:var(--gw);color:var(--ink);
  transition:border-color .15s,background .15s}
.ai-chat-input:focus{border-color:var(--ac);background:var(--g);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--ac) 20%,transparent)}
.ai-chat-send{padding:0 20px;border-radius:14px;border:none;color:var(--on);
  font-family:inherit;font-weight:800;font-size:.88rem;cursor:pointer;background:var(--ac);
  box-shadow:inset 0 2px 1px #fff7,0 6px 14px -6px var(--ac);
  transition:transform .15s}
.ai-chat-send:hover{transform:translateY(-1px)}
.ai-chat-send:active{transform:translateY(1px)}
.ai-chat-send:disabled{opacity:.55;cursor:wait;transform:none}

:focus-visible{outline:2px solid var(--ac);outline-offset:3px;border-radius:12px}
button:focus-visible,a:focus-visible,input:focus-visible{outline-offset:2px}

@media(max-width:760px){
  .win{grid-template-columns:1fr;margin:60px 12px 130px;border-radius:30px;min-height:auto}
  .win aside{display:flex;gap:6px;overflow-x:auto;padding:12px;max-height:none;
    border-inline-end:0;border-bottom:1px solid var(--gb)}
  .win aside .k{display:none}
  .unit{white-space:nowrap;width:auto;flex-shrink:0;background:var(--gw);border:1px solid var(--gb);
    padding:8px 14px;border-radius:99px;margin-top:0;font-size:.82rem}
  .unit.on{background:var(--ac);color:var(--on);border-color:var(--ac)}
  .unit.on i{background:var(--on);opacity:.9;box-shadow:none}
  .unit i{display:none}
  .main{padding:18px}
  .card{padding:20px;border-radius:28px}
  .card h2{font-size:1.1rem}
  .row{font-size:.82rem;padding:7px 10px}
  .ring{width:64px;height:64px}
  .nav a{padding:9px 14px;font-size:.82rem}
  .lens{display:none}
  .win,.mb,.ai-chat-panel{-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px)}
  .mb{padding:6px 14px;gap:8px;font-size:.76rem}
  #toastArea{bottom:96px}
}
@media(max-width:400px){
  .row{grid-template-columns:1fr 1fr}
  .row > :nth-child(3){display:none}
}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;
    transition-duration:.01ms!important}
}
</style>
</head>
<body>

<!-- ============ Scene (خلفية) ============ -->
<div class="scene" aria-hidden="true">
  <svg preserveAspectRatio="none">
    <filter id="cf">
      <feTurbulence type="fractalNoise" baseFrequency=".010 .016" numOctaves="2" seed="3">
        <animate attributeName="baseFrequency" dur="26s"
          values=".010 .016;.018 .010;.010 .016" repeatCount="indefinite"/>
      </feTurbulence>
      <feColorMatrix type="matrix"
        values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 -11 0 0 0 4.9"/>
    </filter>
    <rect width="100%" height="100%" filter="url(#cf)"/>
  </svg>
  <i class="pb" style="--s:120px;--x:4%;--y:62%"></i>
  <i class="pb" style="--s:70px;--x:90%;--y:46%;animation-delay:-3s"></i>
  <i class="pb" style="--s:44px;--x:70%;--y:86%;animation-delay:-6s"></i>
</div>

<!-- ============ Liquid refraction filter ============ -->
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <filter id="lg" x="0" y="0" width="100%" height="100%">
    <feTurbulence type="fractalNoise" baseFrequency=".008 .012" numOctaves="2" seed="4" result="n"/>
    <feDisplacementMap in="SourceGraphic" in2="n" scale="34" xChannelSelector="R" yChannelSelector="G"/>
  </filter>
</svg>

<!-- ============ System bar ============ -->
<header class="mb" id="topBar"></header>

<!-- ============ Main window ============ -->
<main class="win" id="winWrap">
  <aside id="sidebar" aria-label="الوحدات"></aside>
  <section class="main" id="app"></section>
</main>

<!-- ============ AI Lens ============ -->
<button class="lens lq" id="aiLens" type="button" aria-label="المساعد الذكي"
        onclick="openAiChat()" style="display:none">AI</button>

<!-- ============ Bottom Nav ============ -->
<nav class="nav lq" id="bottomNav" style="display:none" aria-label="التنقل">
  <i class="ind" aria-hidden="true"></i>
  <a data-nav="home" onclick="goHome()" class="on" role="button" tabindex="0">القواعد</a>
  <a data-nav="vault" onclick="goVault()" role="button" tabindex="0">الفخاخ</a>
  <a data-nav="progress" onclick="goProgress()" role="button" tabindex="0">تقدمي</a>
</nav>

<!-- ============ Toast Area ============ -->
<div id="toastArea" aria-live="polite" aria-atomic="true"></div>

<!-- ============ AI Chat Overlay ============ -->
<div class="ai-chat-overlay" id="aiChatOverlay" style="display:none"
     onclick="if(event.target===this)closeAiChat()">
  <div class="ai-chat-panel" role="dialog" aria-modal="true" aria-label="مساعد STEP الذكي">
    <div class="ai-chat-head">
      <div class="ai-chat-icon" aria-hidden="true">AI</div>
      <div class="ai-chat-titles">
        <div class="ai-chat-name">مساعد STEP الذكي</div>
        <div class="ai-chat-sub" id="aiChatCardName">اسأل عن هذا الكارد</div>
      </div>
      <button class="ai-chat-close" type="button" aria-label="إغلاق" onclick="closeAiChat()">✕</button>
    </div>
    <div class="ai-chat-messages" id="aiChatMessages">
      <div class="ai-msg ai-msg-bot">
        <div class="ai-msg-body">أهلاً! اسألني أي شي عن هذا الكارد، وأنا أشرح لك بأسلوب مبسّط.</div>
      </div>
    </div>
    <div class="ai-chat-input-row">
      <input id="aiChatInput" class="ai-chat-input" type="text" placeholder="اكتب سؤالك..."
        aria-label="سؤالك"
        onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault();sendAiQuestion();}">
      <button id="aiChatSend" class="ai-chat-send" type="button" onclick="sendAiQuestion()">إرسال</button>
    </div>
  </div>
</div>

<script>
/* ============================================================
   Pearl Reef — STEP Coach (Stable)
   ============================================================ */
const $ = s => document.querySelector(s);
const esc = t => String(t==null?'':t).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const api = async (url, body) => {
  const opts = body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {};
  const res = await fetch(url, opts);
  if (!res.ok) {
    try { const j = await res.json(); return {error:true, status:res.status, ...j}; }
    catch(e){ return {error:true, status:res.status}; }
  }
  return res.json();
};

/* ---------- Theme ---------- */
function initTheme(){
  const R = document.documentElement;
  let sv = null;
  try { sv = localStorage.getItem('pr-theme'); } catch(e){}
  if (sv === 'light' || sv === 'dark') R.dataset.theme = sv;
  else R.dataset.theme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}
function toggleTheme(){
  const R = document.documentElement;
  R.dataset.theme = R.dataset.theme === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('pr-theme', R.dataset.theme); } catch(e){}
}
initTheme();

/* ---------- Toast ---------- */
function toast(msg, type){
  const area = $('#toastArea');
  if (!area) return;
  const t = document.createElement('div');
  t.className = 'toast' + (type ? ' ' + type : '');
  t.textContent = msg;
  area.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.remove(), 400);
  }, 2800);
}

/* ---------- Study Mode Tracking ---------- */
let STUDY_MODE = null;

function studyLabel(mode){
  return ({
    lesson: 'الدرس',
    reading: 'القراءة',
    listening: 'الاستماع',
    drill: 'الاختبار',
    vault: 'دفتر الفخاخ'
  })[mode] || 'المذاكرة';
}

/* ---------- Nav ---------- */
function setupNav(){
  const nav = document.querySelector('.nav');
  if (!nav) return;
  const ind = nav.querySelector('.ind');
  const moveInd = () => {
    const a = nav.querySelector('a.on');
    if (a && ind) {
      ind.style.cssText = `left:${a.offsetLeft}px;top:${a.offsetTop}px;width:${a.offsetWidth}px;height:${a.offsetHeight}px`;
    }
  };
  nav.querySelectorAll('a').forEach(a => {
    a.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); a.click(); }
    });
  });
  new ResizeObserver(moveInd).observe(nav);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveInd);
  setTimeout(moveInd, 100);
}
function setActiveNav(name){
  const nav = document.querySelector('.nav');
  if (!nav) return;
  nav.querySelectorAll('a').forEach(a => a.classList.toggle('on', a.dataset.nav === name));
  const a = nav.querySelector('a.on');
  const ind = nav.querySelector('.ind');
  if (a && ind) {
    ind.style.cssText = `left:${a.offsetLeft}px;top:${a.offsetTop}px;width:${a.offsetWidth}px;height:${a.offsetHeight}px`;
  }
}

/* ---------- Card pointer light (CSS vars only — no reflow) ---------- */
let rafCard = null;
addEventListener('pointermove', e => {
  if (rafCard) return;
  rafCard = requestAnimationFrame(() => {
    rafCard = null;
    const cards = document.querySelectorAll('.card');
    if (!cards.length) return;
    cards.forEach(c => {
      const r = c.getBoundingClientRect();
      if (r.width === 0) return;
      c.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      c.style.setProperty('--my', (e.clientY - r.top) + 'px');
    });
  });
}, {passive:true});

/* ---------- State ---------- */
let APP = { user:null, days:[], vaultCount:0 };
let currentAudio = null;
let currentAudioEl = null;
let CURRENT_WORDS = null;
let CURRENT_AI_CTX = null;

const ROLE_COLORS = {
  subject:'#5cc9c4', verb:'#7bc9c4', aux:'#8eb5cf', noun:'#c9b78b',
  prep:'#d3a3b5', conj:'#a9c0d6', article:'#9fb0b8', adj:'#e49a82',
  adv:'#88c5b5', poss:'#b8b0d2', modal:'#d99b8a', rel:'#96aed0',
  quant:'#a8c592', neg:'#94a4ae', refl:'#c8a3ce', signal:'#d9c07a',
  num:'#b0a89a', time:'#b4a4d2', q:'#8ec5bd', part:'#a8b2b8',
  expletive:'#aab0b4', punct:'#93a4ab', object:'#e49a82', default:'#a8b2b8'
};

function render(html){
  $('#app').innerHTML = html;
  window.scrollTo({top:0, behavior:'auto'});
}
function showChrome(on, solo){
  const mb = $('#topBar');
  if (mb) mb.style.display = on ? 'flex' : 'none';
  const nav = $('#bottomNav');
  if (nav) nav.style.display = on ? 'flex' : 'none';
  const win = $('#winWrap');
  if (win) win.classList.toggle('solo', !!solo);
  const lens = $('#aiLens');
  if (lens) lens.style.display = (on || solo) ? 'grid' : 'none';
}

/* ---------- AI Context ---------- */
function aiBtn(ctx){
  CURRENT_AI_CTX = ctx || {};
  return '';
}

/* ---------- AI Chat ---------- */
function openAiChat(){
  const overlay = $('#aiChatOverlay');
  if (!overlay) return;
  const ctx = CURRENT_AI_CTX || {};
  const name = ctx.title || ctx.rule || 'هذا الكارد';
  const subEl = $('#aiChatCardName');
  if (subEl) subEl.textContent = name.length > 46 ? name.slice(0, 44) + '…' : name;
  overlay.style.display = 'flex';
  setTimeout(() => { const i = $('#aiChatInput'); if (i) i.focus(); }, 60);
}
function closeAiChat(){
  const overlay = $('#aiChatOverlay');
  if (overlay) overlay.style.display = 'none';
}
function sanitizeAiHtml(html){
  if (!html) return '';
  return String(html)
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/\son\w+\s*=\s*"[^"]*"/gi, '')
    .replace(/\son\w+\s*=\s*'[^']*'/gi, '')
    .replace(/javascript:/gi, '');
}
function formatAiResponse(rawText){
  if (!rawText) return '';
  let text = String(rawText);
  text = text.replace(/```html\s*([\s\S]*?)\s*```/gi, '$1');
  text = text.replace(/```\s*([\s\S]*?)\s*```/gi, '$1');
  text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  // إذا ما فيه أي وسم بلوكي، لف النص في <p>
  const hasBlocks = /<(div|p|ul|ol|table|section|article|span|h\d)/i.test(text);
  if (!hasBlocks) {
    text = '<p>' + text.replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br>') + '</p>';
  }
  // ضمان أن span.k يكون block إذا كان في سياق فقرات
  return sanitizeAiHtml(text);
}
function addAiMessage(role, text){
  const msgs = $('#aiChatMessages');
  if (!msgs) return;
  const row = document.createElement('div');
  row.className = 'ai-msg ' + (role === 'user' ? 'ai-msg-user' : 'ai-msg-bot');
  const body = document.createElement('div');
  body.className = 'ai-msg-body';
  if (role === 'user') body.textContent = text;
  else body.innerHTML = formatAiResponse(text);
  row.appendChild(body);
  msgs.appendChild(row);
  msgs.scrollTop = msgs.scrollHeight;
  return row;
}
function addAiThinking(){
  const msgs = $('#aiChatMessages');
  if (!msgs) return null;
  const row = document.createElement('div');
  row.className = 'ai-msg ai-msg-bot';
  row.innerHTML = '<div class="ai-msg-body"><span class="ai-thinking">يكتب<span>.</span><span>.</span><span>.</span></span></div>';
  msgs.appendChild(row);
  msgs.scrollTop = msgs.scrollHeight;
  return row;
}
async function sendAiQuestion(){
  const input = $('#aiChatInput');
  const sendBtn = $('#aiChatSend');
  if (!input || !sendBtn || sendBtn.disabled) return;
  const question = input.value.trim();
  if (!question) return;
  addAiMessage('user', question);
  input.value = '';
  sendBtn.disabled = true;
  const oldLabel = sendBtn.textContent;
  sendBtn.textContent = '...';
  const thinking = addAiThinking();
  const ctx = CURRENT_AI_CTX || {};
  try {
    const r = await api('/api/ai/ask', {
      question: question,
      card_title: ctx.title || '',
      card_body: ctx.body || '',
      card_kind: ctx.kind || '',
      rule_title: ctx.rule || '',
      user_name: (APP.user && APP.user.name) || ''
    });
    if (thinking) thinking.remove();
    if (r.error){
      if (r.error === 'no_key') addAiMessage('bot', 'لم يتم إعداد مفتاح OpenRouter في السيرفر. راجع تعليمات التشغيل.');
      else if (r.error === 'empty_question') addAiMessage('bot', 'اكتب سؤالك أولاً.');
      else if (r.error === 'network') addAiMessage('bot', 'تعذّر الاتصال بالخدمة. تحقق من الإنترنت.');
      else addAiMessage('bot', 'صار خطأ. جرّب مرة أخرى.');
    } else {
      addAiMessage('bot', r.answer || 'ما وصلني رد.');
    }
  } catch(e){
    if (thinking) thinking.remove();
    addAiMessage('bot', 'صار خطأ في الاتصال. جرّب مرة أخرى.');
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = oldLabel || 'إرسال';
    input.focus();
  }
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape'){
    const overlay = $('#aiChatOverlay');
    if (overlay && overlay.style.display === 'flex') closeAiChat();
  }
});

/* ---------- TTS ---------- */
function stopAudio(){
  if (currentAudio){ try{currentAudio.pause();}catch(e){} try{currentAudio.src='';}catch(e){} currentAudio=null; }
  if (currentAudioEl) currentAudioEl.classList.remove('playing');
  currentAudioEl = null;
}
function speak(text, lang, btn){
  lang = lang || 'en';
  stopAudio();
  if (btn){ btn.classList.add('playing'); currentAudioEl = btn; }
  const url = '/tts?t=' + encodeURIComponent(text) + '&lang=' + lang;
  const audio = new Audio(url);
  currentAudio = audio;
  audio.addEventListener('ended', () => { if (currentAudioEl) currentAudioEl.classList.remove('playing'); currentAudio=null; currentAudioEl=null; });
  audio.addEventListener('error', () => { if (currentAudioEl) currentAudioEl.classList.remove('playing'); currentAudio=null; currentAudioEl=null; });
  audio.play().catch(()=>{ if (currentAudioEl) currentAudioEl.classList.remove('playing'); currentAudio=null; currentAudioEl=null; });
}
function spkBtn(text, lang){
  lang = lang || 'en';
  return '<button class="spk" type="button" aria-label="اسمع" data-text="'+esc(text)+'" data-lang="'+lang+'" onclick="event.stopPropagation();speak(this.dataset.text,this.dataset.lang,this)">▶</button>';
}

/* ---------- Word Analysis ---------- */
function renderWordAnalysis(st){
  CURRENT_WORDS = st.words;
  const cellsHTML = st.words.map((w, i) => {
    const role = w.role || 'default';
    const c = ROLE_COLORS[role] || ROLE_COLORS.default;
    return '<div class="wa-cell" data-idx="'+i+'" style="--wc:'+c+'" onclick="selectWaWord('+i+')" role="button" tabindex="0">'+
      '<div class="wa-en">'+esc(w.en)+'</div>'+
      '<div class="wa-arrow">↓</div>'+
      '<div class="wa-ar">'+esc(w.ar)+'</div>'+
      '<div class="wa-role">'+esc(w.role_ar)+'</div>'+
    '</div>';
  }).join('');
  const legend = Object.keys(ROLE_COLORS).filter(k => st.words.some(w => w.role === k)).map(k => {
    const c = ROLE_COLORS[k];
    return '<div class="wa-legend-item"><span class="wa-legend-dot" style="background:'+c+'"></span>'+esc(roleNameAr(k))+'</div>';
  }).join('');
  return '<div class="wa-sentence" dir="ltr">'+cellsHTML+'</div>'+
    '<div class="wa-legend">'+legend+'</div>'+
    '<p class="wa-hint">👆 اضغط على أي كلمة عربية عشان تشوف <b>تفصيلها الكامل</b></p>'+
    '<div class="wa-detail" id="waDetail"></div>'+
    '<div class="gold" style="text-align:center">📖 '+esc(st.translation_ar)+'</div>';
}
function roleNameAr(r){
  const map = {subject:'فاعل', verb:'فعل', aux:'فعل مساعد', noun:'اسم', prep:'حرف جر',
    conj:'أداة ربط', article:'أداة', adj:'صفة', adv:'ظرف', poss:'ملكية', modal:'فعل ناقص',
    rel:'ضمير موصول', quant:'صفة كمية', neg:'نفي', refl:'انعكاسي', signal:'كلمة إشارة',
    num:'عدد', time:'زمن', q:'أداة استفهام', part:'أداة', expletive:'حرف وجود',
    punct:'ترقيم', object:'مفعول به', default:'كلمة'};
  return map[r] || 'كلمة';
}
function selectWaWord(idx){
  document.querySelectorAll('.wa-cell').forEach(c => c.classList.remove('active'));
  const cell = document.querySelector('.wa-cell[data-idx="'+idx+'"]');
  if (!cell) return;
  cell.classList.add('active');
  const w = CURRENT_WORDS[idx];
  const role = w.role || 'default';
  const c = ROLE_COLORS[role] || ROLE_COLORS.default;
  const detail = $('#waDetail');
  if (!detail) return;
  detail.innerHTML = '<div class="wa-detail-inner" style="--wd-color:'+c+'">'+
    '<div class="wa-head">'+
      '<span class="wa-word-big">'+esc(w.en)+'</span>'+
      '<span class="wa-type-tag">'+esc(w.role_ar)+'</span>'+
      spkBtn(w.en)+
    '</div>'+
    '<div class="wa-detail-row">📖 <b>المعنى بالعربي:</b> '+esc(w.ar)+'</div>'+
    (w.note ? '<div class="wa-detail-row">🎯 <b>دورها في الجملة:</b> '+esc(w.note)+'</div>' : '')+
  '</div>';
}

/* ---------- Sidebar ---------- */
function renderSidebar(activeDay){
  const days = APP.days || [];
  let html = '<span class="k">الوحدات</span>';
  days.forEach(d => {
    let cls = 'unit';
    if (d.complete) cls += ' done';
    else if (!d.unlocked) cls += ' locked';
    if (d.n === activeDay) cls += ' on';
    const click = d.unlocked ? 'onclick="startDay('+d.n+')"' : 'disabled';
    html += '<button type="button" class="'+cls+'" '+click+'>'+
      '<i aria-hidden="true"></i>اليوم '+d.n+'</button>';
  });
  return html;
}
function updateSidebar(activeDay){
  const sb = $('#sidebar');
  if (sb) sb.innerHTML = renderSidebar(activeDay);
}

/* ---------- Top Bar ---------- */
function renderTopBar(sub1, sub2){
  return '<b>STEP Coach</b>' +
    (sub1 ? '<span class="sb">'+esc(sub1)+'</span>' : '') +
    (sub2 ? '<span class="sb">'+esc(sub2)+'</span>' : '') +
    '<button class="t" type="button" onclick="toggleTheme()" aria-label="تبديل الوضع">◐ الوضع</button>' +
    '<em>'+ (APP.user ? esc(APP.user.name) : '') +'</em>';
}
function updateTopBar(sub1, sub2){
  const tb = $('#topBar');
  if (tb) tb.innerHTML = renderTopBar(sub1, sub2);
}

/* ---------- Onboarding ---------- */
function screenOnboard(){
  showChrome(false, true);
  updateTopBar();
  updateSidebar(null);
  render('<div class="col" style="margin-top:20px">'+
    '<div class="card" style="text-align:center;padding:38px 26px">'+
      '<div class="ring" style="width:84px;height:84px;margin:0 auto 16px">'+
        '<svg viewBox="0 0 84 84"><circle class="t0" cx="42" cy="42" r="36"/>'+
        '<circle class="t1" cx="42" cy="42" r="36" stroke-dashoffset="60"/>'+
        '<text x="42" y="48" text-anchor="middle">STEP</text></svg>'+
      '</div>'+
      '<h1 style="font-size:1.6rem;font-weight:800;margin-bottom:6px">STEP Coach</h1>'+
      '<p style="color:var(--mut);font-size:.85rem;margin-bottom:22px">قواعد + قراءة + استماع · 8 أيام</p>'+
      '<label style="display:block;text-align:start;font-size:.78rem;font-weight:700;margin-bottom:6px">اسمك</label>'+
      '<input id="inpName" class="ai-chat-input" placeholder="اكتب اسمك" style="width:100%;text-align:start" autocomplete="off">'+
      '<label style="display:block;text-align:start;font-size:.78rem;font-weight:700;margin:16px 0 6px">الدرجة المستهدفة: <span id="tgVal" style="color:var(--ac);font-weight:800">80</span></label>'+
      '<input type="range" id="inpTarget" min="40" max="100" value="80" oninput="document.getElementById(\'tgVal\').textContent=this.value" style="width:100%;accent-color:var(--ac)">'+
      '<p id="err" style="color:var(--ac2);font-size:.78rem;margin-top:10px;min-height:18px"></p>'+
      '<div class="btn-row"><button class="btn" type="button" onclick="doOnboard()">ابدأ رحلتي</button></div>'+
    '</div>'+
  '</div>');
  setTimeout(()=>{ const el = $('#inpName'); if (el) el.focus(); }, 120);
}
async function doOnboard(){
  const name = $('#inpName').value.trim();
  const target = +$('#inpTarget').value;
  if (!name){ $('#err').textContent = 'اكتب اسمك'; return; }
  const r = await api('/api/onboard', {name, target});
  if (r.error){ $('#err').textContent = 'خطأ'; return; }
  await loadState();
  screenHome();
}
async function loadState(){
  const s = await api('/api/state');
  if (!s.user){ screenOnboard(); return false; }
  APP.user = s.user; APP.days = s.days; APP.vaultCount = s.vault;
  return true;
}

/* ---------- Home ---------- */
async function screenHome(){
  STUDY_MODE = null;
  const ok = await loadState(); if (!ok) return;
  showChrome(true, false);
  updateTopBar();
  updateSidebar(null);
  setActiveNav('home');
  const done = APP.days.filter(d => d.complete).length;
  const pct = Math.round(done / 8 * 100);
  const offset = 226 - (226 * pct / 100);
  let journey = '';
  APP.days.forEach(d => {
    const orbCls = d.complete ? 'orb orb-ok' : (d.unlocked ? 'orb' : 'orb orb-lock');
    const icon = d.complete ? '✓' : (d.unlocked ? d.n : '🔒');
    const chips = [];
    if (d.of > 0) chips.push('<span class="chip">📘 '+d.lessons+'/'+d.of+'</span>');
    if (d.reading_of > 0) chips.push('<span class="chip">📖 '+d.reading+'/'+d.reading_of+'</span>');
    if (d.listening_of > 0) chips.push('<span class="chip">🎧 '+d.listening+'/'+d.listening_of+'</span>');
    if (d.vault > 0) chips.push('<span class="chip" style="color:var(--ac2)">🗂 '+d.vault+'</span>');
    journey += '<button type="button" class="day-row '+(d.unlocked ? '' : 'locked')+'" '+(d.unlocked ? 'onclick="startDay('+d.n+')"' : 'disabled')+'>'+
      '<div class="'+orbCls+'">'+icon+'</div>'+
      '<div class="day-body"><div class="day-title">اليوم '+d.n+' — '+esc(d.title)+'</div>'+
      (chips.length ? '<div class="chips">'+chips.join('')+'</div>' : '')+'</div></button>';
  });
  render(
    '<div class="top"><div>'+
      '<span class="k">رحلة ٨ أيام</span>'+
      '<h1>أهلاً '+esc(APP.user.name)+'</h1>'+
      '<p style="color:var(--mut);font-size:.85rem;margin-top:6px">هدفك '+APP.user.target+'+ · المستوى '+(APP.user.level||1)+' · '+done+'/8 أيام مكتملة</p>'+
    '</div>'+
    '<div class="ring"><svg viewBox="0 0 84 84">'+
      '<circle class="t0" cx="42" cy="42" r="36"/>'+
      '<circle class="t1" cx="42" cy="42" r="36" style="stroke-dashoffset:'+offset+'"/>'+
      '<text x="42" y="48" text-anchor="middle">'+pct+'٪</text>'+
    '</svg></div></div>'+
    '<div class="col">'+
      '<div class="card">'+
        '<h2>🕒 خطة اليوم · ٣ ساعات</h2>'+
        '<div class="plan-grid">'+
          '<div class="plan-item"><span>📘</span><span class="plan-lbl">قواعد</span><span class="plan-time">60د</span></div>'+
          '<div class="plan-item"><span>📖</span><span class="plan-lbl">قراءة</span><span class="plan-time">45د</span></div>'+
          '<div class="plan-item"><span>🎧</span><span class="plan-lbl">استماع</span><span class="plan-time">45د</span></div>'+
          '<div class="plan-item"><span>🎯</span><span class="plan-lbl">اختبار</span><span class="plan-time">30د</span></div>'+
        '</div>'+
      '</div>'+
      '<div class="card">'+
        '<h2>🗺️ الوحدات</h2>'+
        '<div class="journey">'+journey+'</div>'+
      '</div>'+
    '</div>'
  );
}

async function startDay(n){
  const d = await api('/api/day/' + n);
  if (d.error){ screenHome(); return; }
  const notDone = d.rules.find(r => !r.done);
  if (notDone){ startLesson(d, n, notDone.id); return; }
  if (d.reading && d.reading.length){ startReading(d, n); return; }
  if (d.listening && d.listening.length){ startListening(d, n); return; }
  if (!d.drill_passed){ startDrill(n); return; }
  if (d.vault_open > 0){ screenVault(n); return; }
  screenHome();
}

/* ---------- Lesson ---------- */
let LESSON = { day:0, rule:null, step:0 };

function startLesson(dayData, dayNum, rid){
  const rule = dayData.rules.find(r => r.id === rid);
  LESSON = { day: dayNum, rule, step: 0 };
  STUDY_MODE = 'lesson';
  showChrome(true, true);
  updateTopBar('اليوم ' + dayNum, rule.title);
  updateSidebar(dayNum);
  setActiveNav('home');
  renderStep();
}

function renderStep(){
  const {rule, step} = LESSON;
  const steps = rule.steps;
  const st = steps[step];
  const total = steps.length;
  let dots = '';
  for (let i = 0; i < total; i++){
    let cls = 'step-dot';
    if (i === step) cls += ' active';
    else if (i < step) cls += ' done';
    dots += '<span class="'+cls+'"></span>';
  }
  let body = '';

  if (st.kind === 'idea'){
    body = '<span class="tag">شرح</span>'+
      '<h2>'+esc(st.title)+'</h2>'+
      '<div class="text-block">'+esc(st.body)+'</div>'+
      (st.reveal ? '<div class="gold"><b>← الخطوة التالية:</b>'+esc(st.reveal)+'</div>' : '');
  }
  else if (st.kind === 'word_analysis'){
    body = '<span class="tag">🔬 تشريح كلمة كلمة</span>'+
      '<h2 class="mid">'+esc(st.title)+'</h2>'+
      '<div class="formula">'+esc(st.sentence)+' '+spkBtn(st.sentence)+'</div>'+
      renderWordAnalysis(st)+
      (st.lesson ? '<div class="gold"><b>الدرس:</b>'+esc(st.lesson)+'</div>' : '')+
      (st.reveal ? '<div class="trap" style="background:color-mix(in srgb,var(--ac) 13%,transparent);border-color:color-mix(in srgb,var(--ac) 45%,transparent)"><b style="color:var(--ac)">← التالي:</b>'+esc(st.reveal)+'</div>' : '');
  }
  else if (st.kind === 'formula'){
    let rows = '';
    st.rows.forEach(r => {
      rows += '<div class="f-row"><div class="f-label">'+esc(r[0])+'</div>'+
        '<div class="formula">'+esc(r[1])+'</div>'+
        '<div class="f-en">'+esc(r[2])+' '+spkBtn(r[2])+'</div></div>';
    });
    body = '<span class="tag">📐 الصيغة</span>'+
      '<h2>'+esc(st.title)+'</h2>'+
      rows+
      (st.note ? '<div class="trap"><b>ملاحظة مهمة:</b><span style="white-space:pre-line">'+esc(st.note)+'</span></div>' : '')+
      (st.reveal ? '<div class="gold"><b>← التالي:</b>'+esc(st.reveal)+'</div>' : '');
  }
  else if (st.kind === 'signals'){
    let items = '';
    st.items.forEach(it => items += '<div class="row c2"><span><b>'+esc(it[0])+'</b></span><span>'+esc(it[1])+'</span></div>');
    body = '<span class="tag">⚡ إشارات</span>'+
      '<h2>'+esc(st.title)+'</h2>'+
      '<div>'+items+'</div>'+
      (st.reveal ? '<div class="gold"><b>← التالي:</b>'+esc(st.reveal)+'</div>' : '');
  }
  else if (st.kind === 'traps'){
    let items = '';
    st.items.forEach(it => items += '<div class="trap"><b>فخ قياس:</b>'+
      '<span style="direction:ltr;display:inline-block;text-align:left;font-family:Inter,monospace;font-weight:700">'+esc(it[0])+'</span>'+
      (it[1] ? '<br><span>'+esc(it[1])+'</span>' : '')+'</div>');
    body = '<span class="tag tag-warn">⚠️ فخاخ</span>'+
      '<h2>'+esc(st.title)+'</h2>'+
      items+
      (st.reveal ? '<div class="gold"><b>← التالي:</b>'+esc(st.reveal)+'</div>' : '');
  }
  else if (st.kind === 'summary'){
    let pts = '';
    st.points.forEach((p, i) => pts += '<div class="row c2"><span><b>'+(i+1)+'</b></span><span>'+esc(p)+'</span></div>');
    body = '<span class="tag">📝 خلاصة</span>'+
      '<h2>'+esc(st.title)+'</h2>'+
      pts;
  }

  const aiCtx = {
    title: st.title || rule.title,
    body: (st.body || '') +
          (st.sentence ? '\n\nالجملة: ' + st.sentence : '') +
          (st.lesson ? '\n\nالدرس: ' + st.lesson : '') +
          (st.note ? '\n\nملاحظة: ' + st.note : '') +
          (st.reveal ? '\n\nإشارة: ' + st.reveal : '') +
          (st.rows ? '\n\n' + st.rows.map(r => r[0]+' → '+r[1]).join('\n') : '') +
          (st.items ? '\n\n' + st.items.map(i => i[0] + ' — ' + i[1]).join('\n') : '') +
          (st.points ? '\n\n' + st.points.join('\n') : ''),
    kind: st.kind || '',
    rule: rule.title || ''
  };
  aiBtn(aiCtx);

  const isLast = step === total - 1;
  let btnHtml = '<div class="btn-row">'+
    '<button class="btn" type="button" onclick="nextStep()">'+(isLast ? 'اختبر نفسك 🧠' : 'التالي ←')+'</button>'+
    (step > 0 ? '<button class="btn gh" type="button" onclick="prevStep()">→ السابق</button>' : '')+
    '</div>';

  render(
    '<div class="top">'+
      '<div><span class="k">'+esc(rule.icon+' '+rule.title)+'</span>'+
      '<h1 class="sm">'+esc(rule.subtitle)+'</h1></div>'+
    '</div>'+
    '<div class="col">'+
      '<div class="steps">'+dots+'</div>'+
      '<div class="card">'+body+btnHtml+'</div>'+
    '</div>'
  );
}
async function nextStep(){
  const r = LESSON.rule;
  api('/api/lesson/'+r.id+'/step/'+LESSON.step, {});
  LESSON.step++;
  if (LESSON.step >= r.steps.length){ startRuleQuiz(); }
  else renderStep();
}
function prevStep(){ if (LESSON.step > 0){ LESSON.step--; renderStep(); } }

/* ---------- Rule Quiz ---------- */
let RQ = { qid:null, day:0, rule:null };
function startRuleQuiz(){
  const r = LESSON.rule;
  RQ = { qid: r.test.id, day: LESSON.day, rule: r };
  renderQuizQuestion(r.test, 'lesson');
}

/* ---------- Quiz ---------- */
let QUIZ = { qs:[], i:0, ctx:'', day:0, thr:0, payload:null, qid_current:0 };

function renderQuizQuestion(q, ctx){
  QUIZ.ctx = ctx;
  const letters = ['A','B','C','D'];
  let optsHTML = '';
  q.opts.forEach((o, j) => {
    optsHTML += '<button class="opt-btn" id="opt-'+j+'" type="button" onclick="quizPick('+j+')"><span class="opt-letter">'+letters[j]+'</span><span>'+esc(o)+'</span></button>';
  });
  let header = '';
  let ctxTitle = 'سؤال';
  if (ctx === 'drill'){
    header = '<div class="timer-track"><div class="timer-fill" id="timerFill" style="width:100%"></div></div>'+
      '<p style="font-size:.76rem;color:var(--mut);font-weight:700">سؤال '+(QUIZ.i+1)+' من '+QUIZ.qs.length+' · النجاح '+QUIZ.thr+'%</p>';
    ctxTitle = 'سؤال اختبار اليوم';
  } else if (ctx === 'reading'){
    header = '<p style="font-size:.76rem;color:var(--mut);font-weight:700">📖 قراءة · سؤال '+(QUIZ.i+1)+' من '+QUIZ.qs.length+'</p>';
    ctxTitle = 'سؤال فهم مقروء';
  } else if (ctx === 'listening'){
    header = '<p style="font-size:.76rem;color:var(--mut);font-weight:700">🎧 استماع · سؤال '+(QUIZ.i+1)+' من '+QUIZ.qs.length+'</p>';
    ctxTitle = 'سؤال فهم مسموع';
  } else {
    header = '<span class="tag">تثبيت</span>';
    ctxTitle = RQ.rule ? ('تثبيت قاعدة ' + RQ.rule.title) : 'تثبيت';
  }

  const optsText = q.opts.map((o, j) => letters[j] + ') ' + o).join('\n');
  aiBtn({
    title: ctxTitle + ': ' + q.stem,
    body: 'نص السؤال: ' + q.stem + '\n\nالخيارات:\n' + optsText +
          (ctx === 'lesson' && RQ.rule ? '\n\nهذا سؤال عن قاعدة: ' + RQ.rule.title : ''),
    kind: 'question_' + ctx,
    rule: (ctx === 'lesson' && RQ.rule ? RQ.rule.title : ctxTitle)
  });

  render(
    '<div class="top"><div><span class="k">'+esc(ctxTitle)+'</span>'+
      '<h1 class="sm">'+esc(q.stem)+'</h1></div></div>'+
    '<div class="col">'+
      '<div class="card">'+header+
        '<div style="margin-top:14px" dir="ltr">'+optsHTML+'</div>'+
        '<div id="fbArea" aria-live="polite"></div>'+
      '</div>'+
    '</div>'
  );
  if (ctx === 'drill'){
    let t = 15;
    const iv = setInterval(() => {
      t--;
      const tf = $('#timerFill');
      if (t <= 0 || !tf){ clearInterval(iv); return; }
      tf.style.width = (t/15*100)+'%';
    }, 1000);
  }
}

async function quizPick(j){
  const q = QUIZ.ctx === 'lesson' ? RQ.rule.test : QUIZ.ctx === 'vault' ? QUIZ.vaultCurrent : QUIZ.qs[QUIZ.i];
  let r;
  if (QUIZ.ctx === 'reading'){ r = await api('/api/reading/'+QUIZ.payload+'/answer', {qid:QUIZ.qid_current, choice:j}); }
  else if (QUIZ.ctx === 'listening'){ r = await api('/api/listening/'+QUIZ.payload+'/answer', {qid:QUIZ.qid_current, choice:j}); }
  else { const ctxMap = {lesson:'lesson', drill:'drill', vault:'vault'}; r = await api('/api/answer', {qid:q.id, choice:j, ctx:ctxMap[QUIZ.ctx], day:QUIZ.day||RQ.day}); }
  $('#opt-'+j).classList.add(r.ok ? 'correct' : 'wrong');
  $('#opt-'+r.ans).classList.add('correct');
  document.querySelectorAll('.opt-btn').forEach(b => b.disabled = true);
  if (r.ok && QUIZ.ctx !== 'lesson') fireConfetti();
  const fb = $('#fbArea');
  fb.innerHTML = '<div class="fb '+(r.ok ? 'fb-ok' : 'fb-bad')+'">'+
    '<div class="fb-title">'+(r.ok ? '✓ صحيح' : '✗ خطأ')+'</div>'+
    '<div class="fb-body">'+
      (r.full_sentence ? '<div class="fb-sent">'+esc(r.full_sentence)+' '+spkBtn(r.full_sentence)+'</div>' : '')+
      (r.tr ? '<p><b>الترجمة:</b> '+esc(r.tr)+'</p>' : '')+
      '<p><b>السبب:</b> '+esc(r.expl)+'</p></div></div>';
  if (QUIZ.ctx === 'lesson'){ fb.innerHTML += '<div class="btn-row"><button class="btn" type="button" onclick="finishRuleQuiz()">ثبّت وكمّل ←</button></div>'; }
  else if (QUIZ.ctx === 'drill'){ const isLast = QUIZ.i >= QUIZ.qs.length - 1; fb.innerHTML += '<div class="btn-row"><button class="btn" type="button" onclick="drillNext()">'+(isLast ? 'شوف النتيجة' : 'التالي ←')+'</button></div>'; }
  else if (QUIZ.ctx === 'reading'){ const isLast = QUIZ.i >= QUIZ.qs.length - 1; fb.innerHTML += '<div class="btn-row"><button class="btn" type="button" onclick="readingNext()">'+(isLast ? 'شوف النتيجة' : 'التالي ←')+'</button></div>'; }
  else if (QUIZ.ctx === 'listening'){ const isLast = QUIZ.i >= QUIZ.qs.length - 1; fb.innerHTML += '<div class="btn-row"><button class="btn" type="button" onclick="listeningNext()">'+(isLast ? 'شوف النتيجة' : 'التالي ←')+'</button></div>'; }
  else if (QUIZ.ctx === 'vault'){ fb.innerHTML += '<div class="btn-row"><button class="btn" type="button" onclick="vaultNext('+(r.ok?1:0)+','+(r.mastered?1:0)+')">التالي ←</button></div>'; }
}

async function finishRuleQuiz(){
  await api('/api/lesson/'+RQ.rule.id, {});
  const d = await api('/api/day/'+RQ.day);
  const notDone = d.rules.find(r => !r.done);
  if (notDone){ startLesson(d, RQ.day, notDone.id); return; }
  if (d.reading && d.reading.length){ startReading(d, RQ.day); return; }
  if (d.listening && d.listening.length){ startListening(d, RQ.day); return; }
  startDrill(RQ.day);
}

/* ---------- Reading ---------- */
let READING = { id:null, data:null, day:0 };
async function startReading(dayData, dayNum){
  const rd = dayData.reading[0];
  const full = await api('/api/reading/'+rd.id);
  READING = { id:rd.id, data:full, day:dayNum };
  STUDY_MODE = 'reading';
  showChrome(true, true);
  updateTopBar('اليوم ' + dayNum, 'قراءة');
  updateSidebar(dayNum);
  renderReadingPassage();
}
function renderReadingPassage(){
  const r = READING.data;
  const passageHTML = esc(r.passage).replace(/\n\n/g, '</p><p>');
  const transHTML = r.translation ? '<div class="rv s"><div><div class="gold" style="margin-top:10px;font-size:.85rem;line-height:1.9">'+esc(r.translation)+'</div></div></div>' : '';
  aiBtn({
    title: 'قطعة قراءة: ' + (r.title_ar || r.title),
    body: 'العنوان: ' + (r.title_ar || '') + ' / ' + (r.title || '') + '\n\nالنص:\n' + r.passage,
    kind: 'reading_passage',
    rule: r.title_ar || r.title || ''
  });
  render(
    '<div class="top"><div><span class="k">📖 قراءة · '+esc(r.level)+'</span>'+
      '<h1 class="sm">'+esc(r.title_ar)+'</h1>'+
      '<p style="color:var(--mut);font-size:.85rem;margin-top:4px">'+esc(r.title)+'</p></div></div>'+
    '<div class="col">'+
      '<div class="card">'+
        '<div class="passage"><p>'+passageHTML+'</p></div>'+transHTML+
        '<div style="background:color-mix(in srgb,var(--ink) 5%,transparent);border:1px solid var(--gb);border-radius:14px;padding:10px 14px;margin-top:14px;font-size:.82rem;color:var(--mut);line-height:1.7">'+
          '📌 <b style="color:var(--ink)">اقرأ القطعة بتركيز كامل، ثم اضغط "ابدأ الأسئلة".</b></div>'+
        '<div class="btn-row"><button class="btn" type="button" onclick="startReadingQuestions()">ابدأ الأسئلة ←</button></div>'+
      '</div>'+
    '</div>'
  );
}
function startReadingQuestions(){
  const r = READING.data;
  QUIZ = { qs: r.questions.map((q,i)=>({stem:q.stem, opts:q.opts, _idx:i})), i: 0, ctx:'reading', payload: READING.id, qid_current: 0 };
  renderReadingQuestion();
}
function renderReadingQuestion(){
  const q = READING.data.questions[QUIZ.i];
  QUIZ.qid_current = QUIZ.i;
  renderQuizQuestion({stem:q.stem, opts:q.opts}, 'reading');
}
async function readingNext(){
  QUIZ.i++;
  if (QUIZ.i >= QUIZ.qs.length) return finishReading();
  renderReadingQuestion();
}
async function finishReading(){
  const r = await api('/api/reading/'+READING.id+'/finish', {});
  const correct = Math.round(r.pct/100 * READING.data.questions.length);
  const total = READING.data.questions.length;
  const offset = 226 - (226 * r.pct / 100);
  render(
    '<div class="top"><div><span class="k">📖 نتيجة القراءة</span><h1 class="sm">'+esc(READING.data.title_ar)+'</h1></div></div>'+
    '<div class="col">'+
      '<div class="card" style="text-align:center">'+
        '<div class="ring" style="width:130px;height:130px;margin:0 auto 8px"><svg viewBox="0 0 84 84">'+
          '<circle class="t0" cx="42" cy="42" r="36"/>'+
          '<circle class="t1" cx="42" cy="42" r="36" style="stroke-dashoffset:'+offset+'"/>'+
          '<text x="42" y="48" text-anchor="middle">'+r.pct+'٪</text>'+
        '</svg></div>'+
        '<p style="font-weight:700;font-size:.92rem;margin-top:6px">'+correct+' من '+total+'</p>'+
        '<div class="btn-row"><button class="btn" type="button" onclick="afterReading()">كمّل ←</button></div>'+
      '</div>'+
    '</div>'
  );
  fireConfetti();
}
async function afterReading(){
  const d = await api('/api/day/'+READING.day);
  if (d.listening && d.listening.length){ startListening(d, READING.day); return; }
  startDrill(READING.day);
}

/* ---------- Listening ---------- */
let LISTENING = { id:null, data:null, day:0 };
async function startListening(dayData, dayNum){
  const ls = dayData.listening[0];
  const full = await api('/api/listening/'+ls.id);
  LISTENING = { id:ls.id, data:full, day:dayNum };
  STUDY_MODE = 'listening';
  showChrome(true, true);
  updateTopBar('اليوم ' + dayNum, 'استماع');
  updateSidebar(dayNum);
  renderListening();
}
function renderListening(){
  const l = LISTENING.data;
  const lines = (l.script || "").split("\n").map(line => {
    const t = line.trim();
    if (!t) return '';
    if (t.startsWith('[VOICE')) return '<span class="script-line directive">'+esc(t)+'</span>';
    if (t.startsWith('[PAUSE')) return '<span class="script-line pause">'+esc(t)+'</span>';
    if (t.startsWith('[SFX')) return '<span class="script-line directive">'+esc(t)+'</span>';
    return '<span class="script-line speech">'+esc(t)+'</span>';
  }).filter(Boolean).join('');
  aiBtn({
    title: 'استماع: ' + (l.title_ar || l.title),
    body: 'الوصف: ' + (l.description || '') + '\n\nنص المحادثة:\n' + (l.script || ''),
    kind: 'listening_script',
    rule: l.title_ar || l.title || ''
  });
  render(
    '<div class="top"><div><span class="k">🎧 استماع · '+esc(l.level)+'</span>'+
      '<h1 class="sm">'+esc(l.title_ar)+'</h1>'+
      '<p style="color:var(--mut);font-size:.85rem;margin-top:4px">'+esc(l.title)+'</p></div></div>'+
    '<div class="col">'+
      '<div class="card">'+
        '<p style="font-weight:700;margin-bottom:12px;font-size:.9rem">'+esc(l.description)+'</p>'+
        '<div class="audio-panel"><button class="play-orb" id="playOrb" type="button" aria-label="تشغيل" onclick="playScript()">▶</button>'+
          '<div class="audio-title">🎧 اسمع المحادثة الكاملة</div>'+
          '<div class="audio-sub">أصوات متعددة + توقفات ذكية تلقائياً</div></div>'+
        '<p style="font-size:.78rem;font-weight:800;color:var(--mut);margin:14px 0 8px">📜 نص المحادثة (للمراجعة بعد الاستماع):</p>'+
        '<div class="script-lines">'+lines+'</div>'+
        '<div class="btn-row"><button class="btn" type="button" onclick="startListeningQuestions()">ابدأ الأسئلة ←</button></div>'+
      '</div>'+
    '</div>'
  );
}
let listeningAudio = null;
function playScript(){
  const orb = $('#playOrb');
  if (!orb) return;
  if (listeningAudio){ try{listeningAudio.pause();}catch(e){} listeningAudio = null; orb.textContent = '▶'; orb.classList.remove('playing'); return; }
  orb.textContent = '⏸'; orb.classList.add('playing');
  listeningAudio = new Audio('/api/listening/'+LISTENING.id+'/audio');
  listeningAudio.addEventListener('ended', ()=>{ if (orb){ orb.textContent = '▶'; orb.classList.remove('playing'); } listeningAudio = null; });
  listeningAudio.addEventListener('error', ()=>{ if (orb){ orb.textContent = '▶'; orb.classList.remove('playing'); } listeningAudio = null; });
  listeningAudio.play().catch(()=>{ if (orb){ orb.textContent = '▶'; orb.classList.remove('playing'); } listeningAudio = null; });
}
function startListeningQuestions(){
  const l = LISTENING.data;
  QUIZ = { qs: l.questions.map((q,i)=>({stem:q.stem, opts:q.opts, _idx:i})), i: 0, ctx:'listening', payload: LISTENING.id, qid_current: 0 };
  renderListeningQuestion();
}
function renderListeningQuestion(){
  const q = LISTENING.data.questions[QUIZ.i];
  QUIZ.qid_current = QUIZ.i;
  renderQuizQuestion({stem:q.stem, opts:q.opts}, 'listening');
}
async function listeningNext(){
  QUIZ.i++;
  if (QUIZ.i >= QUIZ.qs.length) return finishListening();
  renderListeningQuestion();
}
async function finishListening(){
  const r = await api('/api/listening/'+LISTENING.id+'/finish', {});
  const offset = 226 - (226 * r.pct / 100);
  render(
    '<div class="top"><div><span class="k">🎧 نتيجة الاستماع</span><h1 class="sm">'+esc(LISTENING.data.title_ar)+'</h1></div></div>'+
    '<div class="col">'+
      '<div class="card" style="text-align:center">'+
        '<div class="ring" style="width:130px;height:130px;margin:0 auto 8px"><svg viewBox="0 0 84 84">'+
          '<circle class="t0" cx="42" cy="42" r="36"/>'+
          '<circle class="t1" cx="42" cy="42" r="36" style="stroke-dashoffset:'+offset+'"/>'+
          '<text x="42" y="48" text-anchor="middle">'+r.pct+'٪</text>'+
        '</svg></div>'+
        '<div class="btn-row"><button class="btn" type="button" onclick="startDrill(LISTENING.day)">كمّل للاختبار ←</button></div>'+
      '</div>'+
    '</div>'
  );
  fireConfetti();
}

/* ---------- Drill ---------- */
async function startDrill(n){
  const d = await api('/api/drill/'+n);
  if (d.error){ screenHome(); return; }
  QUIZ = { qs:d.qs, i:0, ctx:'drill', day:n, thr:d.thr };
  STUDY_MODE = 'drill';
  showChrome(true, true);
  updateTopBar('اليوم ' + n, 'اختبار');
  updateSidebar(n);
  renderQuizQuestion(d.qs[0], 'drill');
}
function drillNext(){
  QUIZ.i++;
  if (QUIZ.i >= QUIZ.qs.length) finishDrill();
  else renderQuizQuestion(QUIZ.qs[QUIZ.i], 'drill');
}
async function finishDrill(){
  const r = await api('/api/drill/'+QUIZ.day+'/finish', {});
  const isPass = r.passed;
  if (isPass) fireConfetti();
  const offset = 226 - (226 * r.pct / 100);
  render(
    '<div class="top"><div><span class="k">🎯 نتيجة الاختبار</span><h1 class="sm">'+(isPass ? 'ممتاز!' : 'حاول مرة ثانية')+'</h1></div></div>'+
    '<div class="col">'+
      '<div class="card" style="text-align:center">'+
        '<div class="ring" style="width:130px;height:130px;margin:0 auto 8px"><svg viewBox="0 0 84 84">'+
          '<circle class="t0" cx="42" cy="42" r="36"/>'+
          '<circle class="t1" cx="42" cy="42" r="36" style="stroke-dashoffset:'+offset+'"/>'+
          '<text x="42" y="48" text-anchor="middle">'+r.pct+'٪</text>'+
        '</svg></div>'+
        '<p style="font-weight:700;font-size:.9rem">المطلوب: '+r.thr+'%</p>'+
        '<div class="btn-row">'+
          '<button class="btn" type="button" onclick="startDay('+QUIZ.day+')">'+(isPass ? 'كمّل ←' : 'أعد المحاولة')+'</button>'+
          '<button class="btn gh" type="button" onclick="goHome()">رجوع للخطة</button>'+
        '</div>'+
      '</div>'+
    '</div>'
  );
}

/* ---------- Vault ---------- */
let VAULT = { day:0, queue:[], total:0 };
async function screenVault(day){
  showChrome(true, false);
  updateTopBar('الفخاخ', null);
  updateSidebar(day || null);
  setActiveNav('vault');
  const d = await api('/api/vault?day=' + (day || 0));
  if (!d.qs || d.qs.length === 0){
    render(
      '<div class="top"><div><span class="k">دفتر الأخطاء</span><h1>الفخاخ</h1></div></div>'+
      '<div class="col">'+
        '<div class="card vault-empty"><div class="big">✓</div>'+
          '<h2>دفتر الأخطاء فاضي</h2>'+
          '<p style="color:var(--mut);font-size:.85rem;margin-top:6px">ما فيه أخطاء تحتاج إتقان.</p>'+
          '<div class="btn-row"><button class="btn" type="button" onclick="'+(day?'startDay('+day+')':'goHome()')+'">كمّل</button></div>'+
        '</div>'+
      '</div>'
    );
    return;
  }
  VAULT = { day: day || 0, queue: d.qs.slice(), total: d.qs.length };
  STUDY_MODE = 'vault';
  renderVaultQ();
}
function renderVaultQ(){
  if (!VAULT.queue.length){
    STUDY_MODE = null;
    render(
      '<div class="top"><div><span class="k">دفتر الأخطاء</span><h1>الفخاخ</h1></div></div>'+
      '<div class="col">'+
        '<div class="card vault-empty"><div class="big">✓</div><h2>أتقنت دفتر الأخطاء</h2>'+
          '<div class="btn-row"><button class="btn" type="button" onclick="'+(VAULT.day?'startDay('+VAULT.day+')':'goHome()')+'">كمّل</button></div>'+
        '</div>'+
      '</div>'
    );
    return;
  }
  const q = VAULT.queue[0];
  QUIZ.vaultCurrent = q; QUIZ.ctx = 'vault'; QUIZ.day = VAULT.day;
  renderQuizQuestion(q, 'vault');
}
function vaultNext(ok, mastered){
  const x = VAULT.queue.shift();
  if (!mastered) VAULT.queue.push(x);
  renderVaultQ();
}

/* ---------- Progress ---------- */
async function goProgress(){
  if (STUDY_MODE){
    toast('خرجت من ' + studyLabel(STUDY_MODE) + ' — تقدمك محفوظ', 'warn');
    STUDY_MODE = null;
  }
  showChrome(true, false);
  updateTopBar('التقدم', null);
  updateSidebar(null);
  setActiveNav('progress');
  const d = await api('/api/progress');
  const rules = d.rules || [];
  let bars = '';
  rules.forEach(r => {
    bars += '<div class="pbar"><span class="pbar-label">'+esc(r.title)+'</span>'+
      '<div class="pbar-track"><div class="pbar-fill" style="width:'+r.pct+'%"></div></div>'+
      '<span class="pbar-pct">'+r.pct+'٪</span></div>';
  });
  const weak = rules.filter(r => r.pct < 60).sort((a,b) => a.pct - b.pct);
  const trainBtn = weak.length ?
    '<div class="btn-row"><button class="btn" type="button" onclick="goVault()">🎯 تدرّب على الأضعف ('+esc(weak[0].title)+')</button></div>' : '';
  render(
    '<div class="top"><div><span class="k">لوحة التقدم</span><h1>تقدمي</h1></div></div>'+
    '<div class="col">'+
      '<div class="card">'+
        '<h2>📊 دقة الإجابات في كل قاعدة</h2>'+
        bars+trainBtn+
      '</div>'+
    '</div>'
  );
}

/* ---------- Navigation (with notifications) ---------- */
function goHome(){
  if (STUDY_MODE){
    toast('خرجت من ' + studyLabel(STUDY_MODE) + ' — تقدمك محفوظ', 'warn');
    STUDY_MODE = null;
  }
  screenHome();
}
function goVault(){
  if (STUDY_MODE && STUDY_MODE !== 'vault'){
    toast('خرجت من ' + studyLabel(STUDY_MODE) + ' — تقدمك محفوظ', 'warn');
    STUDY_MODE = null;
  }
  screenVault(0);
}

/* ---------- Confetti ---------- */
function fireConfetti(){
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const colors = ['#5cc9c4','#e49a82','#bff5ee','#ffd9c7','#a9c0d6'];
  for (let i = 0; i < 16; i++){
    const el = document.createElement('div');
    el.className = 'confetti-piece';
    el.style.left = Math.random() * 100 + '%';
    el.style.top = '-10px';
    el.style.background = colors[Math.floor(Math.random() * colors.length)];
    el.style.animationDelay = Math.random() * 0.5 + 's';
    el.style.animationDuration = (2 + Math.random() * 1.3) + 's';
    document.body.appendChild(el);
    el.addEventListener('animationend', () => el.remove());
  }
}

/* ---------- Init ---------- */
(async function(){
  setupNav();
  const s = await api('/api/state');
  if (s.user){ APP.user = s.user; APP.days = s.days || []; APP.vaultCount = s.vault || 0; screenHome(); }
  else { screenOnboard(); }
})();
</script>
</body></html>{% endraw %}"""


@app.get("/")
def index():
    return render_template_string(PAGE)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)