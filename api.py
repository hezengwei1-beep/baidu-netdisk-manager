"""百度网盘 API 封装"""

import hashlib
import os
import time
from pathlib import Path

import requests
from tqdm import tqdm

BASE_URL = "https://pan.baidu.com/rest/2.0/xpan"
UPLOAD_URL = "https://d.pcs.baidu.com/rest/2.0/pcs/superfile2"

# 百度网盘分片大小：4MB
CHUNK_SIZE = 4 * 1024 * 1024


class BaiduPanAPI:
    """百度网盘 API 客户端"""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.session = requests.Session()
        self.session.params = {"access_token": self.access_token}

    # ── 空间信息 ──

    def quota(self) -> dict:
        """查询空间用量"""
        resp = self.session.get(
            "https://pan.baidu.com/api/quota",
            params={"access_token": self.access_token, "checkfree": 1, "checkexpire": 1},
        )
        resp.raise_for_status()
        return resp.json()

    def uinfo(self) -> dict:
        """查询用户信息"""
        resp = self.session.get(f"{BASE_URL}/nas", params={
            "access_token": self.access_token,
            "method": "uinfo",
        })
        resp.raise_for_status()
        return resp.json()

    # ── 文件列表 ──

    def list_dir(self, path: str = "/", start: int = 0, limit: int = 1000,
                 order: str = "name", desc: int = 0) -> list[dict]:
        """获取指定目录下的文件列表"""
        resp = self.session.get(f"{BASE_URL}/file", params={
            "access_token": self.access_token,
            "method": "list",
            "dir": path,
            "start": start,
            "limit": limit,
            "order": order,
            "desc": desc,
            "web": "web",
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("errno", 0) != 0:
            raise RuntimeError(f"列表失败 [{path}]: errno={data['errno']}")
        return data.get("list", [])

    def list_all(self, path: str = "/", recursion: int = 1, start: int = 0,
                 limit: int = 1000) -> list[dict]:
        """递归获取文件列表（自动处理 API 分页上限）"""
        all_files = []
        while True:
            resp = self.session.get(f"{BASE_URL}/multimedia", params={
                "access_token": self.access_token,
                "method": "listall",
                "path": path,
                "recursion": recursion,
                "start": start,
                "limit": limit,
                "web": "web",
            })
            if resp.status_code == 400:
                # listall API 的 start 有上限，切换到逐目录遍历
                break
            resp.raise_for_status()
            data = resp.json()
            if data.get("errno", 0) != 0:
                raise RuntimeError(f"递归列表失败 [{path}]: errno={data['errno']}")
            files = data.get("list", [])
            if not files:
                break
            all_files.extend(files)
            if data.get("has_more", 0) == 0:
                break
            start += limit
        return all_files

    def walk_dir(self, path: str = "/", on_batch=None) -> list[dict]:
        """逐目录遍历（适用于大量文件，无 API 分页上限问题）"""
        all_files = []
        skipped_dirs = []
        dirs_to_scan = [path]

        while dirs_to_scan:
            current_dir = dirs_to_scan.pop(0)
            start = 0
            while True:
                try:
                    items = self.list_dir(current_dir, start=start, limit=1000)
                except Exception as e:
                    skipped_dirs.append((current_dir, str(e)))
                    break
                if not items:
                    break
                for item in items:
                    all_files.append(item)
                    if item.get("isdir", 0):
                        dirs_to_scan.append(item["path"])
                if on_batch:
                    on_batch(len(all_files), current_dir)
                if len(items) < 1000:
                    break
                start += 1000

        if skipped_dirs and on_batch:
            on_batch(len(all_files), f"[完成] 跳过 {len(skipped_dirs)} 个无法访问的目录")

        return all_files

    def file_meta(self, fsids: list[int]) -> list[dict]:
        """查询文件元信息（含 MD5）"""
        import json
        resp = self.session.get(f"{BASE_URL}/multimedia", params={
            "access_token": self.access_token,
            "method": "filemetas",
            "fsids": json.dumps(fsids),
            "dlink": 1,
            "thumb": 0,
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("errno", 0) != 0:
            raise RuntimeError(f"查询文件信息失败: errno={data['errno']}")
        return data.get("list", [])

    def search(self, key: str, dir: str = "/", recursion: int = 1,
               page: int = 1, num: int = 500) -> list[dict]:
        """搜索文件"""
        resp = self.session.get(f"{BASE_URL}/file", params={
            "access_token": self.access_token,
            "method": "search",
            "key": key,
            "dir": dir,
            "recursion": recursion,
            "page": page,
            "num": num,
            "web": "web",
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("errno", 0) != 0:
            raise RuntimeError(f"搜索失败 [{key}]: errno={data['errno']}")
        return data.get("list", [])

    # ── 文件操作 ──

    def file_manage(self, opera: str, filelist: list[dict]) -> dict:
        """文件管理操作（移动/重命名/复制/删除）

        opera: move / rename / copy / delete
        filelist: 操作列表，格式取决于 opera 类型
          move/copy: [{"path": "/src", "dest": "/dest_dir", "newname": "name"}]
          rename: [{"path": "/src", "newname": "name"}]
          delete: ["/path1", "/path2"]
        """
        import json
        data = {"async": 0, "filelist": json.dumps(filelist), "ondup": "fail"}
        resp = self.session.post(
            f"{BASE_URL}/file",
            params={"access_token": self.access_token, "method": "filemanager", "opera": opera},
            data=data,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("errno", 0) != 0:
            raise RuntimeError(f"文件操作 [{opera}] 失败: errno={result['errno']}, info={result.get('info')}")
        return result

    def move(self, file_list: list[dict]) -> dict:
        """移动文件
        file_list: [{"path": "/old/path.txt", "dest": "/new/dir", "newname": "path.txt"}]
        """
        return self.file_manage("move", file_list)

    def rename(self, file_list: list[dict]) -> dict:
        """重命名文件
        file_list: [{"path": "/dir/old.txt", "newname": "new.txt"}]
        """
        return self.file_manage("rename", file_list)

    def delete(self, paths: list[str]) -> dict:
        """删除文件
        paths: ["/path/to/file1", "/path/to/file2"]
        """
        return self.file_manage("delete", paths)

    def mkdir(self, path: str) -> dict:
        """创建目录"""
        resp = self.session.post(f"{BASE_URL}/file", params={
            "access_token": self.access_token,
            "method": "create",
        }, data={
            "path": path,
            "size": 0,
            "isdir": 1,
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("errno", 0) not in (0, -8):  # -8 表示目录已存在
            raise RuntimeError(f"创建目录失败 [{path}]: errno={data['errno']}")
        return data

    # ── 上传 ──

    def upload_file(self, local_path: str, remote_path: str) -> dict:
        """上传文件（自动选择直接上传或分片上传）"""
        file_size = os.path.getsize(local_path)
        if file_size <= CHUNK_SIZE:
            return self._upload_single(local_path, remote_path)
        return self._upload_sliced(local_path, remote_path)

    def _upload_single(self, local_path: str, remote_path: str) -> dict:
        """直接上传（小于 4MB 的文件）"""
        # 预创建
        with open(local_path, "rb") as f:
            content = f.read()
        block_md5 = hashlib.md5(content).hexdigest()
        content_md5 = block_md5

        precreate_resp = self.session.post(f"{BASE_URL}/file", params={
            "access_token": self.access_token,
            "method": "precreate",
        }, data={
            "path": remote_path,
            "size": len(content),
            "isdir": 0,
            "autoinit": 1,
            "block_list": f'["{block_md5}"]',
            "content-md5": content_md5,
        })
        precreate_resp.raise_for_status()
        pre_data = precreate_resp.json()
        if pre_data.get("errno", 0) != 0:
            raise RuntimeError(f"预创建失败: {pre_data}")

        upload_id = pre_data["uploadid"]

        # 上传分片
        upload_resp = self.session.post(
            UPLOAD_URL,
            params={
                "access_token": self.access_token,
                "method": "upload",
                "type": "tmpfile",
                "path": remote_path,
                "uploadid": upload_id,
                "partseq": 0,
            },
            files={"file": content},
        )
        upload_resp.raise_for_status()

        # 创建文件
        create_resp = self.session.post(f"{BASE_URL}/file", params={
            "access_token": self.access_token,
            "method": "create",
        }, data={
            "path": remote_path,
            "size": len(content),
            "isdir": 0,
            "uploadid": upload_id,
            "block_list": f'["{block_md5}"]',
        })
        create_resp.raise_for_status()
        return create_resp.json()

    def _upload_sliced(self, local_path: str, remote_path: str) -> dict:
        """分片上传（大于 4MB 的文件）"""
        file_size = os.path.getsize(local_path)

        # 计算每个分片的 MD5
        block_md5_list = []
        with open(local_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                block_md5_list.append(hashlib.md5(chunk).hexdigest())

        import json
        # 预创建
        precreate_resp = self.session.post(f"{BASE_URL}/file", params={
            "access_token": self.access_token,
            "method": "precreate",
        }, data={
            "path": remote_path,
            "size": file_size,
            "isdir": 0,
            "autoinit": 1,
            "block_list": json.dumps(block_md5_list),
        })
        precreate_resp.raise_for_status()
        pre_data = precreate_resp.json()
        if pre_data.get("errno", 0) != 0:
            raise RuntimeError(f"预创建失败: {pre_data}")

        upload_id = pre_data["uploadid"]

        # 逐片上传
        with open(local_path, "rb") as f:
            for i, _ in enumerate(tqdm(block_md5_list, desc="上传中", unit="片")):
                chunk = f.read(CHUNK_SIZE)
                upload_resp = self.session.post(
                    UPLOAD_URL,
                    params={
                        "access_token": self.access_token,
                        "method": "upload",
                        "type": "tmpfile",
                        "path": remote_path,
                        "uploadid": upload_id,
                        "partseq": i,
                    },
                    files={"file": chunk},
                )
                upload_resp.raise_for_status()

        # 创建文件
        create_resp = self.session.post(f"{BASE_URL}/file", params={
            "access_token": self.access_token,
            "method": "create",
        }, data={
            "path": remote_path,
            "size": file_size,
            "isdir": 0,
            "uploadid": upload_id,
            "block_list": json.dumps(block_md5_list),
        })
        create_resp.raise_for_status()
        return create_resp.json()

    # ── 下载 ──

    def download_file(self, dlink: str, local_path: str):
        """通过 dlink 下载文件"""
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        resp = self.session.get(
            dlink,
            params={"access_token": self.access_token},
            headers={"User-Agent": "pan.baidu.com"},
            stream=True,
        )
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        with open(local_path, "wb") as f:
            with tqdm(total=total, unit="B", unit_scale=True, desc=Path(local_path).name) as pbar:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    pbar.update(len(chunk))

    def get_dlink(self, fsid: int) -> str:
        """获取文件下载链接"""
        metas = self.file_meta([fsid])
        if not metas:
            raise RuntimeError(f"获取下载链接失败: fsid={fsid}")
        dlink = metas[0].get("dlink", "")
        if not dlink:
            raise RuntimeError(f"文件无下载链接: fsid={fsid}")
        return dlink
