#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频关联管理模块 - 处理视频-图片关联相关的所有功能

包含两个主要类：
1. VideoRelationDialog - 为单张图片设置视频关联的对话框
2. RelationManager - 批量扫描和管理视频关联
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import threading
import json
import subprocess
import platform

from ui_components import DynamicToolTip


class VideoRelationDialog:
    """视频关联设置对话框 - 为单张图片选择主视频"""
    
    def __init__(self, parent, db_manager, config_manager, current_file_info, debug_utils=None,
                 get_bookmark_callback=None):
        self.parent = parent
        self.db = db_manager
        self.config = config_manager
        self.current_file = current_file_info
        self.debug = debug_utils
        self.get_bookmark_root = get_bookmark_callback  # 获取书签根目录的回调
        self.result = False
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("设置视频关联 - 选择主视频")
        self.dialog.geometry("800x600")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # 提取当前文件的番号
        self.current_code = self._extract_code_from_path(self.current_file.get('file_path', ''))
        
        self._create_ui()
        self._load_candidate_videos_by_code()
    
    def _create_ui(self):
        main_frame = ttk.Frame(self.dialog, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 当前文件信息
        info_frame = ttk.LabelFrame(main_frame, text="当前关联视频（仅含缩略图）", padding=5)
        info_frame.pack(fill=tk.X, pady=5)
        
        current_path = self.current_file.get('file_path', 'N/A')
        ttk.Label(info_frame, text=f"路径: {current_path}", wraplength=700).pack(anchor=tk.W)
        ttk.Label(info_frame, text=f"文件夹: {os.path.dirname(current_path)}").pack(anchor=tk.W)
        if self.current_code:
            ttk.Label(info_frame, text=f"识别番号: {self.current_code}", 
                     foreground="blue", font=("微软雅黑", 9, "bold")).pack(anchor=tk.W)
        
        # 主视频选择区
        select_frame = ttk.LabelFrame(main_frame, text="选择主视频（存储真实视频文件）", padding=5)
        select_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 路径输入
        path_frame = ttk.Frame(select_frame)
        path_frame.pack(fill=tk.X, pady=5)
        
        self.path_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.path_var, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(path_frame, text="浏览...", command=self._browse_video).pack(side=tk.LEFT, padx=5)
        ttk.Button(path_frame, text="按番号搜索...", command=self._load_candidate_videos_by_code).pack(side=tk.LEFT, padx=5)
        
        # 候选视频列表
        list_frame = ttk.Frame(select_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 添加说明标签
        if self.current_code:
            hint_text = f"下方列出的是书签文件夹中包含番号 [{self.current_code}] 或相似番号的视频"
        else:
            hint_text = "下方列出的是书签文件夹中的所有视频（未识别到番号）"
        ttk.Label(select_frame, text=hint_text, foreground="gray", font=("微软雅黑", 8)).pack(anchor=tk.W, pady=(0, 5))
        
        # 树形列表
        columns = ('folder', 'code', 'path', 'size')
        self.candidate_tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=12)
        self.candidate_tree.heading('folder', text='所在文件夹')
        self.candidate_tree.heading('code', text='番号')
        self.candidate_tree.heading('path', text='视频路径')
        self.candidate_tree.heading('size', text='大小')
        
        self.candidate_tree.column('folder', width=200)
        self.candidate_tree.column('code', width=100)
        self.candidate_tree.column('path', width=350)
        self.candidate_tree.column('size', width=80)
        
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.candidate_tree.yview)
        self.candidate_tree.configure(yscrollcommand=scrollbar.set)
        
        self.candidate_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.candidate_tree.bind('<Double-1>', self._on_candidate_select)
        
        # 按钮区
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(btn_frame, text="确认关联", command=self._confirm).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=self.dialog.destroy).pack(side=tk.RIGHT, padx=5)
        
        self.status_label = ttk.Label(main_frame, text="", foreground="blue")
        self.status_label.pack(fill=tk.X, pady=5)
    
    def _extract_code_from_path(self, path):
        """从路径提取番号"""
        from core import PathUtils
        folder_name = os.path.basename(os.path.dirname(path))
        file_name = os.path.splitext(os.path.basename(path))[0]
        
        code = PathUtils.extract_code_from_folder_name(file_name)
        if not code:
            code = PathUtils.extract_code_from_folder_name(folder_name)
        return code
    
    def _browse_video(self):
        video_path = filedialog.askopenfilename(
            title="选择主视频文件",
            filetypes=[("视频文件", "*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm *.m4v *.mpeg *.mpg"), ("所有文件", "*.*")]
        )
        if video_path:
            self.path_var.set(os.path.normpath(video_path))
            self.status_label.config(text=f"已手动选择: {video_path}", foreground="green")
    
    def _load_candidate_videos_by_code(self):
        """基于番号搜索候选视频（改进版）"""
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        
        if not self.db or not self.db.is_ready:
            self.status_label.config(text="数据库未就绪", foreground="red")
            return
        
        # 获取搜索根目录（书签根目录）
        search_root = None
        if self.get_bookmark_root:
            search_root = self.get_bookmark_root()
        
        if not search_root:
            # 如果没有获取到书签根目录，使用所有书签
            search_roots = []
            for dev_id, device in self.config.devices.items():
                for bm in device.get('bookmarks', []):
                    search_roots.append(bm['path'])
            if not search_roots:
                self.status_label.config(text="未找到书签目录，无法搜索", foreground="red")
                return
        else:
            search_roots = [search_root]
        
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            candidates = []
            
            # 搜索所有视频文件（限制在书签目录下）
            for root in search_roots:
                root_pattern = root + '%'
                cursor.execute("""
                    SELECT file_path, file_name, file_size, folder_id 
                    FROM media_files 
                    WHERE file_type = 'video' 
                    AND file_path LIKE ?
                    AND (is_alias = 0 OR is_alias IS NULL)
                    AND file_path != ?
                """, (root_pattern, self.current_file.get('file_path', '')))
                
                for row in cursor.fetchall():
                    video_path = row['file_path']
                    folder_path = os.path.dirname(video_path)
                    folder_name = os.path.basename(folder_path)
                    video_name = os.path.splitext(row['file_name'])[0]
                    
                    # 提取文件夹和文件的番号
                    folder_code = self._extract_code_from_path(folder_path + '/dummy')
                    file_code = self._extract_code_from_path(video_path)
                    
                    # 计算匹配度
                    score = 0
                    match_reason = ""
                    
                    if self.current_code:
                        # 当前文件有番号，进行匹配
                        if self.current_code.upper() == (folder_code or '').upper():
                            score = 100  # 完全匹配文件夹番号
                            match_reason = "文件夹番号匹配"
                        elif self.current_code.upper() == (file_code or '').upper():
                            score = 90   # 完全匹配文件名番号
                            match_reason = "文件名番号匹配"
                        elif folder_code and self.current_code.upper() in folder_code.upper():
                            score = 70
                            match_reason = "文件夹番号包含"
                        elif file_code and self.current_code.upper() in file_code.upper():
                            score = 60
                            match_reason = "文件名番号包含"
                        else:
                            # 不匹配，但如果是同一根目录也显示（低优先级）
                            score = 10
                            match_reason = "同书签下"
                    else:
                        # 当前文件无番号，显示所有
                        score = 1
                        match_reason = "无番号匹配"
                    
                    if score >= 10:  # 至少有点关系
                        size_mb = f"{row['file_size'] / 1024 / 1024:.1f} MB" if row['file_size'] else "未知"
                        display_code = folder_code if folder_code else (file_code if file_code else "-")
                        
                        candidates.append({
                            'score': score,
                            'folder': folder_name,
                            'code': display_code,
                            'path': video_path,
                            'size': size_mb,
                            'reason': match_reason
                        })
            
            cursor.close()
            
            # 按匹配度排序
            candidates.sort(key=lambda x: x['score'], reverse=True)
            
            # 显示前50个
            for c in candidates[:50]:
                # 格式化路径显示：最后一级文件夹/文件名
                display_path = f"{c['folder']}/{os.path.basename(c['path'])}"
                item_id = self.candidate_tree.insert('', 'end', values=(
                    c['folder'], c['code'], display_path, c['size']
                ))
                # 高亮完全匹配项
                if c['score'] >= 90:
                    self.candidate_tree.item(item_id, tags=('perfect_match',))
                elif c['score'] >= 60:
                    self.candidate_tree.item(item_id, tags=('good_match',))
            
            # 设置标签颜色
            self.candidate_tree.tag_configure('perfect_match', background='#90EE90')  # 浅绿
            self.candidate_tree.tag_configure('good_match', background='#FFFACD')   # 浅黄
            
            self.status_label.config(
                text=f"找到 {len(candidates)} 个候选视频（按番号 [{self.current_code or '无'}] 匹配排序）", 
                foreground="blue"
            )
            
        except Exception as e:
            self.status_label.config(text=f"搜索失败: {e}", foreground="red")
            import traceback
            traceback.print_exc()
    
    def _on_candidate_select(self, event):
        selection = self.candidate_tree.selection()
        if selection:
            item = self.candidate_tree.item(selection[0])
            video_path = item['values'][2]  # path列
            self.path_var.set(video_path)
            self.status_label.config(text=f"已选择: {video_path}", foreground="green")
    
    def _confirm(self):
        primary_path = self.path_var.get().strip()
        if not primary_path:
            messagebox.showwarning("提示", "请先选择主视频")
            return
        
        if not os.path.exists(primary_path):
            messagebox.showerror("错误", f"主视频文件不存在:\n{primary_path}")
            return
        
        try:
            # 步骤1: 确保文件夹在数据库中（内部会自行管理连接）
            folder_id = self._ensure_folder_in_db(os.path.dirname(primary_path))
            if not folder_id:
                messagebox.showerror("错误", "无法创建或获取文件夹记录")
                return
            
            # 步骤2: 检查主视频是否已在数据库中，不存在则插入
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT file_id FROM media_files WHERE file_path = ?", (primary_path,))
            result = cursor.fetchone()
            
            if not result:
                cursor.execute("""
                    INSERT INTO media_files (folder_id, file_path, file_name, file_type, file_size, last_modified)
                    VALUES (?, ?, ?, 'video', ?, datetime('now'))
                """, (folder_id, primary_path, os.path.basename(primary_path), 
                     os.path.getsize(primary_path)))
                primary_id = cursor.lastrowid
                conn.commit()
            else:
                primary_id = result['file_id']
            
            cursor.close()
            
            # 步骤3: 建立关联关系（set_video_relation内部使用独立连接）
            alias_id = self.current_file.get('id') or self.current_file.get('file_id')
            success = self.db.set_video_relation(alias_id, primary_id)
            
            if success:
                self.result = True
                messagebox.showinfo("成功", "视频关联已建立！")
                self.dialog.destroy()
            else:
                messagebox.showerror("错误", "关联建立失败")
                
        except Exception as e:
            messagebox.showerror("错误", f"操作失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _ensure_folder_in_db(self, folder_path):
        """确保文件夹记录在数据库中存在，返回folder_id"""
        conn = None
        cursor = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            # 先查询是否已存在
            cursor.execute("SELECT folder_id FROM folders WHERE folder_path = ?", (folder_path,))
            result = cursor.fetchone()
            if result:
                return result['folder_id']
            
            # 不存在则插入新记录
            parent = os.path.dirname(folder_path) if folder_path != os.path.dirname(folder_path) else None
            cursor.execute("""
                INSERT INTO folders (folder_path, folder_name, parent_path, last_modified)
                VALUES (?, ?, ?, datetime('now'))
            """, (folder_path, os.path.basename(folder_path), parent))
            folder_id = cursor.lastrowid
            conn.commit()
            return folder_id
        except Exception as e:
            if self.debug:
                self.debug.print(f"_ensure_folder_in_db 失败: {e}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            if cursor:
                cursor.close()
    
    def show(self):
        self.dialog.wait_window()
        return self.result, self.path_var.get()


class RelationManager:
    """视频关联管理器 - 处理批量关联建议、管理和操作"""
    
    def __init__(self, parent, db_manager, config_manager, debug_utils=None):
        self.parent = parent
        self.db = db_manager
        self.config = config_manager
        self.debug = debug_utils
        self.root = parent if isinstance(parent, tk.Tk) else parent.winfo_toplevel()
    
    # ==================== 关联建议对话框 ====================
    
    def create_relation_dialog_async(self, on_refresh_callback=None):
        """创建异步扫描的关联建议对话框 - 支持动态Tooltip和文件夹名显示"""
        dialog = tk.Toplevel(self.root)
        dialog.title("关联建议 - 实时扫描中...")
        dialog.geometry("1300x700")
        dialog.minsize(1000, 500)
        dialog.transient(self.root)

        info_label = ttk.Label(dialog, text="正在扫描数据库，请稍候...", foreground="blue")
        info_label.pack(pady=5)

        main_frame = ttk.Frame(dialog)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        tree_frame = ttk.LabelFrame(main_frame, text="候选关联列表（实时更新）")
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        columns = ('primary_path', 'alias_path', 'code')
        tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=12, selectmode='extended')
        tree.heading('primary_path', text='主视频路径')
        tree.heading('alias_path', text='关联图片/文件夹')
        tree.heading('code', text='匹配番号')
        tree.column('primary_path', width=500)
        tree.column('alias_path', width=500)
        tree.column('code', width=120)

        scrollbar_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar_y.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)

        # 下部分：双栏预览
        preview_frame = ttk.LabelFrame(main_frame, text="文件列表预览（点击上方行查看）")
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        paned = ttk.PanedWindow(preview_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # 左侧面板
        left_container = ttk.Frame(paned)
        right_container = ttk.Frame(paned)
        paned.add(left_container, weight=1)
        paned.add(right_container, weight=1)
        
        # 左侧超链接标题和列表
        left_title = tk.Label(left_container, text="主视频所在文件夹", 
                             fg="blue", cursor="hand2", 
                             font=("微软雅黑", 9, "underline"))
        left_title.pack(anchor=tk.W, padx=5, pady=2)
        
        left_listbox = tk.Listbox(left_container, font=("Consolas", 9), selectmode=tk.SINGLE)
        left_scroll = ttk.Scrollbar(left_container, orient=tk.VERTICAL, command=left_listbox.yview)
        left_listbox.configure(yscrollcommand=left_scroll.set)
        left_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 右侧超链接标题和列表
        right_title = tk.Label(right_container, text="关联图片/文件夹所在文件夹", 
                              fg="blue", cursor="hand2", 
                              font=("微软雅黑", 9, "underline"))
        right_title.pack(anchor=tk.W, padx=5, pady=2)
        
        right_listbox = tk.Listbox(right_container, font=("Consolas", 9), selectmode=tk.SINGLE)
        right_scroll = ttk.Scrollbar(right_container, orient=tk.VERTICAL, command=right_listbox.yview)
        right_listbox.configure(yscrollcommand=right_scroll.set)
        right_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 绑定点击打开文件夹事件
        left_title.bind("<Button-1>", lambda e: self._open_folder_from_title(left_title, left_listbox))
        right_title.bind("<Button-1>", lambda e: self._open_folder_from_title(right_title, right_listbox))

        # 点击行时更新标题显示
        def on_tree_select(event):
            left_listbox.delete(0, tk.END)
            right_listbox.delete(0, tk.END)
            selected = tree.selection()
            if not selected:
                left_title.config(text="主视频所在文件夹")
                right_title.config(text="关联图片/文件夹所在文件夹")
                left_title.folder_path = None
                right_title.folder_path = None
                return
            
            item = selected[0]
            tags = tree.item(item, 'tags')
            if not tags:
                return
            
            try:
                sug = json.loads(tags[0])
            except:
                import ast
                try:
                    sug = ast.literal_eval(tags[0])
                except:
                    return

            # 主视频文件夹处理
            try:
                primary_video = sug.get('primary_video')
                if not primary_video:
                    raise ValueError("primary_video 不存在")
                primary_path = primary_video.get('file_path')
                if not primary_path:
                    raise ValueError("primary_video 缺少 file_path")
                primary_folder = os.path.dirname(primary_path)
                primary_folder_name = os.path.basename(primary_folder)
                
                left_title.config(text=f"主视频所在文件夹 ({primary_folder_name})")
                left_title.folder_path = primary_folder
                
                for f in sorted(os.listdir(primary_folder)):
                    left_listbox.insert(tk.END, f)
            except Exception as e:
                left_listbox.insert(tk.END, f"读取失败: {str(e)}")
                left_title.config(text="主视频所在文件夹 (读取失败)")
                left_title.folder_path = None

            # 关联文件夹处理
            try:
                if sug.get('type') == 'folder':
                    alias_folder = sug.get('folder_path')
                else:
                    img = sug.get('image', {})
                    alias_folder = os.path.dirname(img.get('file_path', ''))
                
                if not alias_folder:
                    raise ValueError("无法获取关联文件夹路径")
                
                alias_folder_name = os.path.basename(alias_folder)
                right_title.config(text=f"关联图片/文件夹所在文件夹 ({alias_folder_name})")
                right_title.folder_path = alias_folder
                
                for f in sorted(os.listdir(alias_folder)):
                    right_listbox.insert(tk.END, f)
            except Exception as e:
                right_listbox.insert(tk.END, f"读取失败: {str(e)}")
                right_title.config(text="关联图片/文件夹所在文件夹 (读取失败)")
                right_title.folder_path = None

        tree.bind('<<TreeviewSelect>>', on_tree_select)

        # 动态Tooltip
        def get_tooltip_text(event):
            row_id = tree.identify_row(event.y)
            col_id = tree.identify_column(event.x)
            
            if not row_id:
                return None
            
            item = tree.item(row_id)
            values = item.get('values', [])
            if not values or len(values) < 2:
                return None
            
            if col_id == '#1':
                primary_path = values[0] if len(values) > 0 else ""
                if primary_path:
                    return f"完整路径: {os.path.dirname(primary_path)}"
            elif col_id == '#2':
                tags = item.get('tags', [])
                if tags:
                    try:
                        sug = json.loads(tags[0])
                        if sug.get('type') == 'folder':
                            return f"完整路径: {sug.get('folder_path', '')}"
                        else:
                            img_path = sug.get('image', {}).get('file_path', '')
                            return f"完整路径: {os.path.dirname(img_path)}" if img_path else values[1]
                    except:
                        return values[1]
            return None
        
        tooltip = DynamicToolTip(tree, get_tooltip_text)

        # 按钮区域
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=10)

        def apply_selected():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("提示", "请至少选择一行要关联的建议")
                return
            if not messagebox.askyesno("确认关联", f"确定要为选中的 {len(selected)} 个建议建立关联关系吗？"):
                return
            count = 0
            conn = self.db._get_connection()
            cursor = conn.cursor()
            for item in selected:
                tags = tree.item(item, 'tags')
                if not tags:
                    continue
                try:
                    sug = json.loads(tags[0])
                except:
                    import ast
                    sug = ast.literal_eval(tags[0])
                primary_id = sug['primary_video']['file_id']
                if sug['type'] == 'folder':
                    for img in sug['images']:
                        img_id = img['file_id']
                        cursor.execute("SELECT 1 FROM video_relations WHERE alias_file_id = ?", (img_id,))
                        if not cursor.fetchone():
                            cursor.execute(
                                "INSERT INTO video_relations (primary_file_id, alias_file_id, alias_folder_path, created_date) VALUES (?, ?, ?, datetime('now'))",
                                (primary_id, img_id, sug['folder_path'])
                            )
                            cursor.execute("UPDATE media_files SET is_alias = 1 WHERE file_id = ?", (img_id,))
                            count += 1
                else:
                    img_id = sug['image']['file_id']
                    cursor.execute("SELECT 1 FROM video_relations WHERE alias_file_id = ?", (img_id,))
                    if not cursor.fetchone():
                        cursor.execute(
                            "INSERT INTO video_relations (primary_file_id, alias_file_id, alias_folder_path, created_date) VALUES (?, ?, ?, datetime('now'))",
                            (primary_id, img_id, os.path.dirname(sug['image']['file_path']))
                        )
                        cursor.execute("UPDATE media_files SET is_alias = 1 WHERE file_id = ?", (img_id,))
                        count += 1
            conn.commit()
            cursor.close()
            conn.close()
            self.db._load_all_to_cache_async()
            messagebox.showinfo("完成", f"成功建立了 {count} 个关联关系")
            dialog.destroy()
            if on_refresh_callback:
                on_refresh_callback()

        ttk.Button(btn_frame, text="应用选中关联", command=apply_selected, width=15).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="全选", command=lambda: tree.selection_add(tree.get_children())).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="反选", command=lambda: tree.selection_toggle(*tree.get_children())).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy, width=10).pack(side=tk.RIGHT, padx=10)

        def add_row(suggestion):
            def _add():
                if not dialog.winfo_exists():
                    return
                if suggestion['type'] == 'folder':
                    primary_path = suggestion['primary_video']['file_path']
                    alias_info = f"[文件夹] {suggestion['folder_path']} ({len(suggestion['images'])}张图片)"
                    code = suggestion['code']
                else:
                    primary_path = suggestion['primary_video']['file_path']
                    alias_info = f"[图片] {suggestion['image']['file_path']}"
                    code = suggestion['code']
                suggestion_json = json.dumps(suggestion, ensure_ascii=False)
                tree.insert('', 'end', values=(primary_path, alias_info, code), tags=(suggestion_json,))
                info_label.config(text=f"已发现 {len(tree.get_children())} 个候选关联...")
            self.root.after(0, _add)

        def finish():
            def _finish():
                if dialog.winfo_exists():
                    info_label.config(text=f"扫描完成，共发现 {len(tree.get_children())} 个候选关联", foreground="green")
                    dialog.title("关联建议 - 扫描完成")
            self.root.after(0, _finish)

        dialog.protocol("WM_DELETE_WINDOW", lambda: dialog.destroy())
        return dialog, add_row, finish
    
    def _open_folder_from_title(self, title_label, listbox):
        """从关联对话框的超链接标题打开文件夹"""
        folder_path = getattr(title_label, 'folder_path', None)
        if folder_path and os.path.exists(folder_path):
            try:
                if platform.system() == 'Windows':
                    subprocess.Popen(['explorer', folder_path], shell=True)
                elif platform.system() == 'Darwin':
                    subprocess.Popen(['open', folder_path])
                else:
                    subprocess.Popen(['xdg-open', folder_path])
            except Exception as e:
                messagebox.showerror("错误", f"无法打开文件夹: {e}")
        else:
            messagebox.showwarning("提示", "文件夹路径无效或不存在")
    
    def scan_for_relation_suggestions(self, on_refresh_callback=None, status_callback=None):
        """异步扫描并建议关联：立即打开窗口，后台逐步添加建议"""
        if not self.db or not self.db.is_ready:
            messagebox.showwarning("提示", "数据库未就绪")
            return

        dialog, add_row_callback, finish_callback = self.create_relation_dialog_async(on_refresh_callback)
        if dialog is None:
            return

        threading.Thread(
            target=self._scan_for_relations_worker,
            args=(add_row_callback, finish_callback),
            daemon=True
        ).start()
        
        if status_callback:
            status_callback("正在后台扫描关联建议...", "blue")
    
    def _scan_for_relations_worker(self, add_row_callback, finish_callback):
        """后台扫描，每找到一个建议就调用 add_row_callback"""
        try:
            from core import extract_code_from_text, get_pattern_matcher
            
            device = self.config.devices.get(self.config.device_id, {})
            bookmarks = [os.path.normpath(bm['path']) for bm in device.get('bookmarks', [])]
            if not bookmarks:
                self.root.after(0, lambda: messagebox.showinfo("提示", "当前设备没有书签"))
                finish_callback()
                return

            conn = self.db._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT mf.file_id, mf.file_path, mf.file_name, f.folder_path
                FROM media_files mf
                JOIN folders f ON mf.folder_id = f.folder_id
                WHERE mf.file_type = 'video' AND (mf.is_deleted = 0 OR mf.is_deleted IS NULL)
            """)
            all_videos = [dict(row) for row in cursor.fetchall()]

            matcher = get_pattern_matcher()
            video_codes = []
            for v in all_videos:
                code_info = extract_code_from_text(v['file_name'])
                if code_info:
                    video_codes.append({
                        'video': v,
                        'code_info': code_info,
                        'code_str': code_info['canonical']
                    })

            cursor.execute("""
                SELECT DISTINCT f.folder_path
                FROM media_files mf
                JOIN folders f ON mf.folder_id = f.folder_id
                WHERE mf.file_type = 'image'
                AND mf.folder_id NOT IN (
                    SELECT DISTINCT folder_id FROM media_files WHERE file_type = 'video'
                )
            """)
            image_only_folders = [row['folder_path'] for row in cursor.fetchall()]

            processed_folders = set()
            processed_images = set()

            def get_images_in_folder(folder):
                cursor.execute("""
                    SELECT mf.file_id, mf.file_path, mf.file_name
                    FROM media_files mf
                    JOIN folders f ON mf.folder_id = f.folder_id
                    WHERE f.folder_path = ? AND mf.file_type = 'image'
                """, (folder,))
                return [dict(img) for img in cursor.fetchall()]

            # 处理仅图片文件夹
            for folder in image_only_folders:
                if not any(folder.startswith(bm) for bm in bookmarks):
                    continue
                if folder in processed_folders:
                    continue

                images = get_images_in_folder(folder)
                if not images:
                    continue

                folder_name = os.path.basename(folder)
                folder_code_info = extract_code_from_text(folder_name)
                if not folder_code_info:
                    folder_code_info = extract_code_from_text(images[0]['file_name'])
                if not folder_code_info:
                    continue

                matched = []
                for vc in video_codes:
                    score = matcher.is_strict_match(folder_code_info, vc['code_info'])
                    if score >= 1:
                        matched.append((score, vc['video']))
                if not matched:
                    continue
                matched.sort(key=lambda x: x[0], reverse=True)
                best_video = matched[0][1]

                processed_folders.add(folder)
                suggestion = {
                    'type': 'folder',
                    'folder_path': folder,
                    'primary_video': best_video,
                    'code': folder_code_info['canonical'],
                    'images': images
                }
                add_row_callback(suggestion)

            # 处理独立图片
            cursor.execute("""
                SELECT mf.file_id, mf.file_path, mf.file_name, f.folder_path
                FROM media_files mf
                JOIN folders f ON mf.folder_id = f.folder_id
                WHERE mf.file_type = 'image'
                AND (mf.is_alias = 0 OR mf.is_alias IS NULL)
            """)
            all_images = [dict(row) for row in cursor.fetchall()]

            for img in all_images:
                img_folder = img['folder_path']
                if img_folder in processed_folders:
                    continue
                cursor.execute("""
                    SELECT 1 FROM media_files
                    WHERE folder_id = (SELECT folder_id FROM media_files WHERE file_path = ?)
                    AND file_type = 'video' LIMIT 1
                """, (img['file_path'],))
                if cursor.fetchone():
                    continue

                code_info = extract_code_from_text(img['file_name'])
                if not code_info:
                    continue

                matched = []
                for vc in video_codes:
                    score = matcher.is_strict_match(code_info, vc['code_info'])
                    if score >= 1:
                        matched.append((score, vc['video']))
                if not matched:
                    continue
                matched.sort(key=lambda x: x[0], reverse=True)
                best_video = matched[0][1]

                img_key = img['file_path']
                if img_key in processed_images:
                    continue
                processed_images.add(img_key)

                suggestion = {
                    'type': 'image',
                    'image': img,
                    'primary_video': best_video,
                    'code': code_info['canonical']
                }
                add_row_callback(suggestion)

            cursor.close()
            conn.close()
            finish_callback()

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("错误", f"扫描失败: {str(e)}"))
            if self.debug:
                self.debug.print(f"扫描线程异常: {e}")
                import traceback
                traceback.print_exc()
            finish_callback()
