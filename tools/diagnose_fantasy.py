"""用现有模型隔离复现开篇；只输出协议概要，不改真实冒险存档。"""
import json
import gc
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nyalume.core import fantasy, interaction, llm, memory
from nyalume.frontends.pet.pet3d import keepsakes


def main():
    with tempfile.TemporaryDirectory(prefix="nyalume_fantasy_check_") as folder:
        database = str(Path(folder) / "copy.db")
        with closing(sqlite3.connect(memory.DB_PATH)) as source, closing(sqlite3.connect(database)) as target:
            source.backup(target)
        memory.DB_PATH = database
        keepsakes.home_path = lambda: Path(folder) / "小窝"
        fantasy.MAX_ROUNDS = 4
        fantasy.FIRST_CHAPTER_ROUNDS = 4
        fantasy.CHAPTER_SECONDS = 240
        fantasy.FIRST_CHAPTER_SECONDS = 240
        original = llm.chat_stream
        def observed(*args, **kwargs):
            parts, names = [], []
            for event in original(*args, **kwargs):
                if event['kind'] == 'content':
                    parts.append(event['text'])
                elif event['kind'] == 'tool_delta' and event.get('name'):
                    names.append(event['name'])
                yield event
            parser = interaction.ReplyStream()
            parser.feed(''.join(parts))
            _, data = parser.finish()
            proposal = data.get('fantasy', {})
            print(json.dumps({'response_chars': sum(map(len, parts)), 'tools': names,
                              'metadata_keys': list(data), 'chapter_keys': list(proposal) if isinstance(proposal, dict) else [],
                              'choice_count': len(data.get('choices', []))}, ensure_ascii=False), flush=True)
        llm.chat_stream = observed
        print('model:', llm.get_model(), flush=True)
        for event in fantasy.run_stream(fantasy.SESSION_ID, '请开启第一章奇幻冒险，让我决定如何参与。'):
            if event['type'] != 'text':
                print(json.dumps({k: event[k] for k in ('type', 'message') if k in event}, ensure_ascii=True), flush=True)
        gc.collect()  # sqlite3 的事务上下文不会关闭连接；释放临时库句柄后再清理目录。


if __name__ == '__main__':
    main()
