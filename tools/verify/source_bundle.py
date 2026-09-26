#!/usr/bin/env python3
"""P3-15 配套 · 把一次决策的**全部字节**打成可离线回放的包，再恢复成一棵树。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：`export` —— 按 EvidenceSet 的冻结血缘收集控制面 + 数据面的字节；
  `restore` —— 把它摊回一棵目录树，让回放能在那棵树上跑
- **不覆盖**：判断回放到底过没过。那是 `data/drills.py:source_zip_replay_drill`，
  它读的是恢复出来那棵树里真实存在的东西

🔴 为什么这几步必须有工具，而不是「操作的时候手敲」
--------------------------------------------------
`SOURCE_ZIP_REPLAY` 这项演练的判据早就写好了，但它的前置操作
（打包 / 解包 / 把库和数据面放到对的相对位置）一直只存在于口头描述里。
于是这项演练**从来没被跑过** —— 不是因为难，是因为每次都要重新想一遍
「URI 是相对谁的」。而那个细节答错一次，回放会报「文件不存在」，
读起来像是数据坏了，实际上只是工作目录不对。

⚠️ 存储 URI 是**相对仓库根**的（`data/lake/...`），不是相对 `data_root`。
所以恢复出来的树必须把 `data/` 放在树根下，并在树根下跑回放。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _verdict as _v  # noqa: E402

#: 包内固定的相对位置。恢复方按这两个位置摊开，回放在树根下跑。
DB_IN_BUNDLE = "data/biga.db"
MANIFEST_IN_BUNDLE = "bundle_manifest.json"


def _referenced_files(evidence_set_id: str, *, db: pathlib.Path) -> tuple[list[str], dict]:
    """走一遍冻结血缘，收集**这次决策真正引用到**的数据面文件。

    🔴 不扫 `data/` 整棵树：包的意义是「这次决策看到的那些字节」。
    多塞进去的文件会让回放在一棵本不该能跑通的树上跑通 —— 那是把
    「我恢复对了」和「我什么都带上了」混为一谈。
    """
    from easyup_biga.data.replay import SQLITE_URI_SCHEME
    from easyup_biga.persistence import connect

    files: list[str] = []
    detail: dict = {"partitions": [], "raw_artifacts": []}
    with connect(db, readonly=True) as conn:
        rows = conn.execute(
            "SELECT dataset_id,snapshot_id FROM evidence_set_datasets WHERE evidence_set_id=?",
            (evidence_set_id,),
        ).fetchall()
        if not rows:
            raise SystemExit(f"EvidenceSet {evidence_set_id} 没有冻结任何 dataset —— 不是可回放的对象")
        for row in rows:
            snap = conn.execute(
                "SELECT manifest_json FROM dataset_snapshots WHERE snapshot_id=?",
                (str(row["snapshot_id"]),),
            ).fetchone()
            manifest = json.loads(str(snap["manifest_json"]))
            for partition_id in manifest.get("partition_ids") or ():
                part = conn.execute(
                    "SELECT storage_uri,content_sha256 FROM dataset_partitions WHERE partition_id=?",
                    (str(partition_id),),
                ).fetchone()
                uri = str(part["storage_uri"])
                detail["partitions"].append({"partition_id": partition_id, "storage_uri": uri})
                if not uri.startswith(SQLITE_URI_SCHEME):
                    files.append(uri)
        for row in conn.execute(
            "SELECT artifact_id,body_uri FROM raw_artifacts WHERE dataset_id IN "
            "(SELECT dataset_id FROM evidence_set_datasets WHERE evidence_set_id=?)",
            (evidence_set_id,),
        ):
            uri = str(row["body_uri"])
            detail["raw_artifacts"].append({"artifact_id": row["artifact_id"], "body_uri": uri})
            if not uri.startswith(SQLITE_URI_SCHEME):
                files.append(uri)
    return sorted(dict.fromkeys(files)), detail


def _consistent_db_copy(src: pathlib.Path, dst: pathlib.Path) -> None:
    """用 `VACUUM INTO` 取一份一致副本 —— 不是 `cp`。

    WAL 模式下直接拷 `.db` 会漏掉 `-wal` 里尚未 checkpoint 的事务，
    而漏掉的部分**不会报错**：恢复出来的库能打开、能查，只是少了最近几条。
    """
    from easyup_biga.persistence import connect

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    with connect(src, readonly=True) as conn:
        conn.execute("VACUUM INTO ?", (str(dst),))


def cmd_export(args: argparse.Namespace) -> int:
    db = pathlib.Path(args.db)
    out = pathlib.Path(args.out)
    files, detail = _referenced_files(args.evidence_set_id, db=db)

    # 🔴 绝对 URI 打不成可恢复的包，必须当场拒绝，而不是打出一个「看起来成功」的包。
    #    `storage_uri` 原样回显发布时传的 `data_root`：传相对路径就存相对、
    #    传绝对路径就存绝对。绝对路径**搬不动** —— 恢复出来的库仍然指着
    #    导出那台机器上的目录，回放会去读一个恰好还在的旧文件，
    #    或者报「文件不存在」。前者比后者糟得多：它会**通过**。
    absolute = [item for item in files if pathlib.Path(item).is_absolute()]
    if absolute:
        print("❌ 冻结血缘里的 storage_uri 是绝对路径，这样的包恢复不出来：")
        for item in absolute:
            print(f"  ❌ {item}")
        print("   发布时 data_root 要传相对路径（生产路径传的是 'data'）。")
        return _v.FAIL

    # ⚠️ 相对 URI 一律按**当前工作目录**解析 —— 与真正的读取方
    #    (`FileStore.verify_file_hash` 的 `Path(uri)`) 用同一条规则。
    #    这里若改成「相对仓库根」，导出与回放会在不同的地方找同一个文件。
    missing = [item for item in files if not pathlib.Path(item).exists()]
    if missing:
        print("❌ 冻结血缘引用的文件在盘上找不到：")
        for item in missing:
            print(f"  ❌ {item}")
        return _v.FAIL

    staging = out.parent / f".{out.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _consistent_db_copy(db, staging / DB_IN_BUNDLE)
        for item in files:
            target = staging / item
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pathlib.Path(item), target)
        manifest = {
            "evidence_set_id": args.evidence_set_id,
            "db_in_bundle": DB_IN_BUNDLE,
            "files": files,
            **detail,
        }
        (staging / MANIFEST_IN_BUNDLE).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(staging).as_posix())
        tmp.replace(out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"✅ 已导出 {out}")
    print(f"  evidence_set_id = {args.evidence_set_id}")
    print(f"  数据面文件 {len(files)} 个，控制面 1 份（VACUUM INTO 一致副本）")
    print(f"  sha256 = {digest}")
    return _v.PASS


def cmd_restore(args: argparse.Namespace) -> int:
    bundle = pathlib.Path(args.bundle)
    into = pathlib.Path(args.into)
    if into.exists() and any(into.iterdir()):
        print(f"❌ {into} 非空 —— 恢复必须落在干净目录上，否则分不清哪些字节来自包")
        return _v.FAIL
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle) as zf:
        zf.extractall(into)
    manifest = json.loads((into / MANIFEST_IN_BUNDLE).read_text(encoding="utf-8"))
    print(f"✅ 已恢复到 {into}")
    print(f"  evidence_set_id = {manifest['evidence_set_id']}")
    print("  下一步 —— 🔴 **不能用 bin/biga-data**：")
    print("    那个壳第一件事就是 cd 回仓库根（为了让默认的 data/ 相对路径成立），")
    print("    于是回放读到的是**原树**的字节，而不是恢复出来这棵树的。")
    print("    它会 PASS，而那个 PASS 与恢复这件事毫无关系。")
    print("")
    print(f"    env -C {into} PYTHONPATH={SRC} \\")
    print("      python3 -m easyup_biga.data.cli \\")
    print(f"      --db {DB_IN_BUNDLE} --data-root data \\")
    print(f"      drill-replay {manifest['evidence_set_id']}")
    return _v.PASS


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    item = sub.add_parser("export")
    item.add_argument("evidence_set_id")
    item.add_argument("--db", default="data/biga.db")
    item.add_argument("--out", required=True)
    item.set_defaults(fn=cmd_export)
    item = sub.add_parser("restore")
    item.add_argument("bundle")
    item.add_argument("--into", required=True)
    item.set_defaults(fn=cmd_restore)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
