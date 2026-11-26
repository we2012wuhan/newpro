# -*- coding: utf-8 -*-
# 每日笔记（页面在 /notes）
# ------------------------------------------------------------
# 一天可以记很多条：文字、标签、最多 9 张图。条目按 day 归档，
# 页面默认打开今天，也可以翻日期和搜索。
#
#   GET    /notes                         页面
#   GET    /notes/api/status              额度、日期列表、标签、限制
#   GET    /notes/api/notes               按天 / 搜索 / 标签筛选
#   GET    /notes/api/notes/{id}          单条
#   POST   /notes/api/notes               新建（multipart，文字 + 图片）
#   PUT    /notes/api/notes/{id}          编辑（multipart，可增删图片）
#   DELETE /notes/api/notes/{id}          删除
#   GET    /notes/api/images/{id}         登录后读取图片
#
# 数据：daily_notes / daily_note_images 在 SQLite；图片本体在 data/notes/。
# 图片只认 JPEG / PNG / WebP / GIF，按文件头判断，不信扩展名。
# 不做自动过期删除——日记是长期数据，只有用户主动删才会消失。
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import storage

router = APIRouter()

TOOL = 'daily-notes'
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'notes.html'
_STORED_RE = re.compile(r'^[0-9a-f]{32}\.(?:jpg|png|webp|gif)$')
_TMP_RE = re.compile(r'^[0-9a-f]{32}$')
_CHUNK = 1024 * 1024

CONTENT_MAX = 10000
TAGS_MAX = 10
TAG_MAX = 20
IMAGES_PER_NOTE = 9
LIST_LIMIT = 300

IMAGE_TYPES = (
    ('image/jpeg', '.jpg'),
    ('image/png', '.png'),
    ('image/webp', '.webp'),
    ('image/gif', '.gif'),
)


class NoteError(RuntimeError):
    """可以直接展示给用户的错误。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.environ.get(name) or '').strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _image_max_bytes() -> int:
    return _env_int('NOTES_IMAGE_MB', 10, 1, 50) * 1024 * 1024


def _total_max_bytes() -> int:
    return _env_int('NOTES_TOTAL_MB', 2048, 64, 102400) * 1024 * 1024


def _root() -> Path:
    raw = (os.environ.get('NOTES_DIR') or '').strip()
    if raw:
        return Path(raw).expanduser()
    return storage.db_path().parent / 'notes'


def _user_key(user: str) -> str:
    return hashlib.sha256(('notes:' + str(user)).encode('utf-8')).hexdigest()[:20]


def _note_dir(user: str, note_id) -> Path:
    return _root() / _user_key(user) / str(int(note_id))


def _user(request: Request) -> str:
    return getattr(request.state, 'user', '') or ''


def _error(message: str, code: int = 400) -> JSONResponse:
    return JSONResponse({'ok': False, 'message': message}, status_code=code)


def _clean_content(value) -> str:
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if len(text) > CONTENT_MAX:
        raise NoteError('单条笔记最多 %d 个字。' % CONTENT_MAX)
    return text


def _clean_day(value, fallback: str = '') -> str:
    text = str(value or '').strip() or str(fallback or '').strip()
    if not text:
        raise NoteError('请选择日期。')
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise NoteError('日期格式不对，用 YYYY-MM-DD。')
    if parsed.year < 1970 or parsed.year > 2100:
        raise NoteError('日期超出可记录范围。')
    return parsed.isoformat()


def _parse_tags(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        text = str(raw).strip()
        if not text:
            return []
        if text.startswith('['):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            values = parsed if isinstance(parsed, list) else re.split(r'[\s,，]+', text)
        else:
            values = re.split(r'[\s,，]+', text)
    clean, seen = [], set()
    for item in values:
        tag = ' '.join(str(item or '').split()).lstrip('#').strip()
        if not tag:
            continue
        if len(tag) > TAG_MAX:
            raise NoteError('单个标签最多 %d 个字。' % TAG_MAX)
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        clean.append(tag)
    if len(clean) > TAGS_MAX:
        raise NoteError('最多加 %d 个标签。' % TAGS_MAX)
    return clean


def _parse_remove_ids(raw) -> list:
    if raw is None or raw == '':
        return []
    if isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        text = str(raw).strip()
        if not text:
            return []
        if text.startswith('['):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            values = parsed if isinstance(parsed, list) else re.split(r'[\s,，]+', text)
        else:
            values = re.split(r'[\s,，]+', text)
    out, seen = [], set()
    for value in values:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            raise NoteError('要删除的图片参数不对。')
        if image_id <= 0:
            raise NoteError('要删除的图片参数不对。')
        if image_id not in seen:
            seen.add(image_id)
            out.append(image_id)
    return out


def _sniff_image(head: bytes):
    if head.startswith(b'\xff\xd8\xff'):
        return '.jpg', 'image/jpeg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png', 'image/png'
    if head.startswith(b'GIF87a') or head.startswith(b'GIF89a'):
        return '.gif', 'image/gif'
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp', 'image/webp'
    return None


def _note_out(note: dict, images) -> dict:
    return {
        'id': note.get('id'),
        'day': note.get('day') or '',
        'content': note.get('content') or '',
        'tags': list(note.get('tags') or []),
        'createdAt': note.get('created_at') or '',
        'updatedAt': note.get('updated_at') or '',
        'images': [
            {
                'id': row.get('id'),
                'url': '/notes/api/images/%s' % row.get('id'),
                'mime': row.get('mime') or '',
                'bytes': int(row.get('bytes') or 0),
            }
            for row in (images or [])
        ],
    }


def _meta() -> dict:
    return {
        'contentMax': CONTENT_MAX,
        'tagsMax': TAGS_MAX,
        'tagMax': TAG_MAX,
        'imagesPerNote': IMAGES_PER_NOTE,
        'imageMaxBytes': _image_max_bytes(),
        'totalBytes': _total_max_bytes(),
        'imageTypes': [mime for mime, _ in IMAGE_TYPES],
    }


async def _stage_uploads(files, budget: int):
    """把图片流式写进临时目录，校验格式 / 单张大小 / 总空间。"""
    files = [file for file in (files or []) if file is not None]
    if not files:
        return None, []
    if budget <= 0:
        raise NoteError('笔记图片空间已经用完，先删掉一些旧图再上传。', 413)

    folder = _root() / '.tmp' / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    staged, total = [], 0
    try:
        for upload in files:
            head = await upload.read(32)
            if not head:
                raise NoteError('有一张图片是空文件。')
            kind = _sniff_image(head)
            if not kind:
                raise NoteError('只支持 JPEG / PNG / WebP / GIF 图片。')
            extension, mime = kind
            await upload.seek(0)
            stored_name = uuid.uuid4().hex + extension
            target = folder / stored_name
            part = folder / (stored_name + '.part')
            size = 0
            try:
                with part.open('wb') as output:
                    while True:
                        chunk = await upload.read(_CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > _image_max_bytes():
                            raise NoteError(
                                '单张图片不能超过 %dMB。' % (_image_max_bytes() // (1024 * 1024)),
                                413)
                        if total + size > budget:
                            raise NoteError('笔记图片空间不够，先删掉一些旧图再上传。', 413)
                        await run_in_threadpool(output.write, chunk)
                if size <= 0:
                    raise NoteError('有一张图片是空文件。')
                part.replace(target)
            except Exception:
                part.unlink(missing_ok=True)
                target.unlink(missing_ok=True)
                raise
            total += size
            staged.append({
                'stored_name': stored_name,
                'mime': mime,
                'bytes': size,
                'path': target,
            })
        return folder, staged
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        for upload in files:
            try:
                await upload.close()
            except Exception:
                pass


def _move_staged(user: str, note_id, staged) -> list:
    """把临时文件移进这条记录的目录，并写图片元数据。"""
    staged = list(staged or [])
    if not staged:
        return []
    folder = _note_dir(user, note_id)
    folder.mkdir(parents=True, exist_ok=True)
    moved = []
    try:
        for item in staged:
            target = folder / item['stored_name']
            item['path'].replace(target)
            moved.append(target)
        rows = storage.daily_note_images_add(user, note_id, staged)
        return rows
    except Exception:
        for target in moved:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _remove_images(user: str, note_id, images) -> None:
    for row in images or []:
        storage.daily_note_image_delete(user, row['id'])
        name = str(row.get('stored_name') or '')
        if _STORED_RE.match(name):
            try:
                (_note_dir(user, note_id) / name).unlink(missing_ok=True)
            except OSError:
                pass


def _drop_note_dir(user: str, note_id) -> None:
    folder = _note_dir(user, note_id)
    try:
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        pass


# ---------------- 页面 ----------------
@router.get('/notes', response_class=HTMLResponse)
def notes_page() -> HTMLResponse:
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui"><h2>模板文件缺失</h2>'
                '<p>请确认 templates/notes.html 存在。</p></body></html>')
    return HTMLResponse(html)


# ---------------- 状态 / 列表 ----------------
@router.get('/notes/api/status')
def notes_status(request: Request, today: str = '') -> JSONResponse:
    user = _user(request)
    try:
        anchor = _clean_day(today, date.today().isoformat())
        data = _meta()
        data.update({
            'ok': True,
            'days': storage.daily_note_days(user),
            'total': storage.daily_note_count(user),
            'tags': storage.daily_note_tags(user),
            'usedBytes': storage.daily_note_image_total(user),
            'streak': storage.daily_note_streak(user, anchor),
        })
    except NoteError as exc:
        return _error(str(exc), 400)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 503)
    return JSONResponse(data)


@router.get('/notes/api/days')
def notes_days(request: Request) -> JSONResponse:
    try:
        days = storage.daily_note_days(_user(request))
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 503)
    return JSONResponse({'ok': True, 'days': days})


@router.get('/notes/api/notes')
def notes_list(request: Request, day: str = '', q: str = '', tag: str = '',
               limit: int = LIST_LIMIT) -> JSONResponse:
    user = _user(request)
    try:
        day = _clean_day(day) if str(day or '').strip() else ''
        rows = storage.daily_note_list(user, day=day, q=q, tag=tag, limit=limit)
        groups = storage.daily_note_image_groups(user, [row['id'] for row in rows])
    except NoteError as exc:
        return _error(str(exc), exc.status)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 503)
    items = [_note_out(row, groups.get(row['id'], [])) for row in rows]
    return JSONResponse({'ok': True, 'items': items, 'count': len(items)})


@router.get('/notes/api/notes/{note_id}')
def notes_get(note_id: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        note = storage.daily_note_get(user, note_id)
        if not note:
            return _error('找不到这条笔记。', 404)
        images = storage.daily_note_image_list(user, note_id)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 503)
    return JSONResponse({'ok': True, 'item': _note_out(note, images)})


# ---------------- 新建 / 编辑 / 删除 ----------------
@router.post('/notes/api/notes')
async def notes_create(
    request: Request,
    content: str = Form(''),
    day: str = Form(''),
    tags: str = Form('[]'),
    images: list[UploadFile] | None = File(default=None),
) -> JSONResponse:
    user = _user(request)
    files = [file for file in (images or []) if file is not None]
    if len(files) > IMAGES_PER_NOTE:
        return _error('一条笔记最多 %d 张图。' % IMAGES_PER_NOTE)
    if not str(content or '').strip() and not files:
        return _error('写点什么，或者至少加一张图。')

    tmp = None
    note_id = None
    success = False
    try:
        text = _clean_content(content)
        note_day = _clean_day(day, time.strftime('%Y-%m-%d'))
        clean_tags = _parse_tags(tags)
        used = storage.daily_note_image_total(user)
        budget = _total_max_bytes() - used
        tmp, staged = await _stage_uploads(files, budget)
        if not text and not staged:
            raise NoteError('写点什么，或者至少加一张图。')

        note = storage.daily_note_add(user, note_day, text, clean_tags)
        note_id = note['id']
        moved = await run_in_threadpool(_move_staged, user, note_id, staged)
        item = _note_out(note, moved)
        success = True
        return JSONResponse({'ok': True, 'item': item})
    except NoteError as exc:
        return _error(str(exc), exc.status)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 503)
    except OSError as exc:
        return _error('保存图片失败：%s' % str(exc)[:160], 500)
    except Exception as exc:
        return _error('保存笔记失败：%s' % str(exc)[:160], 500)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        if not success and note_id is not None:
            # 只有走到失败分支才收回半条记录；成功后 finally 也会执行，不能误删。
            try:
                storage.daily_note_delete(user, note_id)
            except Exception:
                pass
            _drop_note_dir(user, note_id)


@router.put('/notes/api/notes/{note_id}')
async def notes_update(
    note_id: int,
    request: Request,
    content: str | None = Form(default=None),
    day: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    removeImages: str = Form(''),
    images: list[UploadFile] | None = File(default=None),
) -> JSONResponse:
    user = _user(request)
    new_files = [file for file in (images or []) if file is not None]
    tmp = None
    moved = []
    success = False
    try:
        note = storage.daily_note_get(user, note_id)
        if not note:
            return _error('找不到这条笔记。', 404)
        current = storage.daily_note_image_list(user, note_id)
        text = _clean_content(content) if content is not None else note.get('content', '')
        note_day = _clean_day(day, note.get('day') or '') if day is not None else note.get('day', '')
        clean_tags = _parse_tags(tags) if tags is not None else list(note.get('tags') or [])
        remove_ids = _parse_remove_ids(removeImages)
        by_id = {int(row['id']): row for row in current}
        if any(image_id not in by_id for image_id in remove_ids):
            return _error('要删除的图片不属于这条笔记。')
        removed = [by_id[image_id] for image_id in remove_ids]
        remaining = len(current) - len(removed)
        if remaining + len(new_files) > IMAGES_PER_NOTE:
            return _error('一条笔记最多 %d 张图。' % IMAGES_PER_NOTE)
        if not text and remaining + len(new_files) == 0:
            return _error('写点什么，或者至少留一张图。')

        used = storage.daily_note_image_total(user)
        freed = sum(int(row.get('bytes') or 0) for row in removed)
        budget = _total_max_bytes() - used + freed
        tmp, staged = await _stage_uploads(new_files, budget)

        moved = await run_in_threadpool(_move_staged, user, note_id, staged)
        affected = storage.daily_note_update(user, note_id, note_day, text, clean_tags)
        if not affected:
            raise NoteError('找不到这条笔记。', 404)
        _remove_images(user, note_id, removed)
        success = True
    except NoteError as exc:
        return _error(str(exc), exc.status)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 503)
    except OSError as exc:
        return _error('保存图片失败：%s' % str(exc)[:160], 500)
    except Exception as exc:
        return _error('保存笔记失败：%s' % str(exc)[:160], 500)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        if not success and moved:
            # 更新失败时把刚加进来的图片收回；成功后 finally 也会执行，不能误删。
            for row in moved:
                try:
                    storage.daily_note_image_delete(user, row['id'])
                except Exception:
                    pass
                name = str(row.get('stored_name') or '')
                if _STORED_RE.match(name):
                    try:
                        (_note_dir(user, note_id) / name).unlink(missing_ok=True)
                    except OSError:
                        pass

    try:
        fresh = storage.daily_note_get(user, note_id)
        images = storage.daily_note_image_list(user, note_id)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 503)
    if not fresh:
        return _error('找不到这条笔记。', 404)
    return JSONResponse({'ok': True, 'item': _note_out(fresh, images)})


@router.delete('/notes/api/notes/{note_id}')
def notes_delete(note_id: int, request: Request) -> JSONResponse:
    user = _user(request)
    try:
        note = storage.daily_note_get(user, note_id)
        if not note:
            return _error('找不到这条笔记。', 404)
        storage.daily_note_delete(user, note_id)
    except storage.StorageUnavailable as exc:
        return _error(str(exc), 503)
    _drop_note_dir(user, note_id)
    return JSONResponse({'ok': True})


# ---------------- 图片 ----------------
@router.get('/notes/api/images/{image_id}')
def notes_image(image_id: int, request: Request):
    user = _user(request)
    try:
        row = storage.daily_note_image_get(user, image_id)
    except storage.StorageUnavailable:
        return _error('图片暂时读不出来。', 503)
    if not row:
        return _error('图片不存在。', 404)
    name = str(row.get('stored_name') or '')
    if not _STORED_RE.match(name):
        return _error('图片不存在。', 404)
    target = _note_dir(user, row.get('note_id')) / name
    if not target.is_file():
        return _error('图片文件已丢失。', 404)
    resp = FileResponse(target, media_type=row.get('mime') or 'application/octet-stream')
    resp.headers['Cache-Control'] = 'private, max-age=604800'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Content-Disposition'] = 'inline'
    return resp


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _app = FastAPI(title='每日笔记', version='1.0.0')
    _app.include_router(router)
    uvicorn.run(_app, host='127.0.0.1', port=8033)
