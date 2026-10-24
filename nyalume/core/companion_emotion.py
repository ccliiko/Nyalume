"""Small local cue matcher for expressed feelings in the chat window."""
import re


def expressed_emotion(text: str) -> str:
    # Require an expressed feeling or an expressive marker, rather than topic words.
    cues = (
        ('sad', r'😭|呜呜|[（(](?:哭|难过)[）)]|(?:我|有点|好|很|真的)(?:好|很|有点)?(?:难过|伤心|失落|想哭)'),
        ('angry', r'😤|[（(]生气[）)]|(?:我|好|很|气死)(?:好|很|有点)?(?:生气|气愤|委屈)'),
        ('shocked', r'😱|[（(](?:震惊|惊呆)[）)]|(?:我|真的)(?:被)?(?:吓到了|惊呆了)'),
        ('confused', r'😵|[（(]困惑[）)]|(?:我|有点)(?:有点)?(?:迷茫|困惑|搞不懂)'),
        ('shy', r'😳|[（(](?:害羞|脸红)[）)]|(?:我|有点)(?:有点)?(?:害羞|不好意思)'),
        ('happy', r'🥰|😄|[（(](?:开心|微笑)[）)]|(?:我|好|很|真的)(?:好|很|有点)?(?:开心|高兴)|太棒了|好耶'),
    )
    for emotion, pattern in cues:
        if re.search(pattern, text):
            return emotion
    return ''
