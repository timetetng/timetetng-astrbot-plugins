from astrbot.api import logger

class LLMModerator:
    def __init__(self, context, config):
        self.context = context
        self.config = config
        self.idx_s1 = 0
        self.idx_s2 = 0

    async def check_content(self, user_msg: str, bot_reply: str, favour_info: str, history: list) -> tuple[bool, str, str]:
        """
        执行审核流程
        Returns: (is_violation, reason, stage_name)
        """
        llm_cfg = self.config.get("llm_detection", {})
        
        # --- 一审 ---
        sys_p1 = f"{favour_info}\n\n{llm_cfg.get('system_prompt', '')}"
        prompt_p1 = f"请审查内容：\n{user_msg}"
        
        is_nsfw, reason = await self._call_llm(
            prompt_p1, sys_p1, llm_cfg.get("stage1_llms", []), "一审", history
        )
        
        if not is_nsfw:
            return False, "", ""

        # --- 二审 (如果开启) ---
        two_stage_cfg = self.config.get("two_stage_review", {})
        if not two_stage_cfg.get("enabled", True):
            return True, reason or "LLM一审判定", "LLM一审"

        logger.info(f"NSFW: 一审存疑 ({reason})，进入二审...")
        
        sys_p2 = f"{favour_info}\n\n{two_stage_cfg.get('stage2_system_prompt', '')}"
        prompt_p2 = f"【用户输入】:\n{user_msg}\n\n【机器人回复】:\n{bot_reply}\n\n---\n请结合上下文复核："
        
        is_nsfw_2, reason_2 = await self._call_llm(
            prompt_p2, sys_p2, two_stage_cfg.get("stage2_llms", []), "二审", history
        )

        if is_nsfw_2:
            return True, reason_2 or reason, "LLM二审"
        
        logger.info("NSFW: 二审判定通过，忽略一审结果。")
        return False, "", ""

    async def _call_llm(self, prompt, system, llm_list, stage, contexts):
        if not llm_list: return False, None
        
        idx_attr = "idx_s1" if stage == "一审" else "idx_s2"
        start_idx = getattr(self, idx_attr)
        count = len(llm_list)

        for i in range(count):
            curr_idx = (start_idx + i) % count
            llm_str = llm_list[curr_idx]
            
            pid, model = llm_str.split(":", 1) if ":" in llm_str else (llm_str.strip(), None)
            
            provider = self.context.get_provider_by_id(pid)
            if not provider: continue

            try:
                resp = await provider.text_chat(
                    prompt=f"{system}\n\n---\n{prompt}",
                    contexts=contexts,
                    model=model
                )
                
                # 轮询索引更新
                setattr(self, idx_attr, (curr_idx + 1) % count)
                
                txt = resp.completion_text.strip()
                if not txt: return True, "API拦截"
                
                if txt.upper().startswith("NSFW"):
                    return True, txt.replace("NSFW:", "").strip() or "未详述"
                return False, None

            except Exception as e:
                logger.warning(f"NSFW {stage}: {pid} 调用失败: {e}")
        
        return False, None
