import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ====== 配置 ======
BOT_TOKEN = "8728500201:AAHG4NtfylhyVDxVuk7MetzoGsmIRSDSuAM"   # 替换成你的真实Token
MAINTENANCE_MARGIN_RATE = 0.005  # 维持保证金率 0.5%，可根据交易所调整
# =================

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# 用户数据存储结构
user_data = {}  # {user_id: {"capital": float, "positions": [{}], "temp": {}}}

def get_user_state(user_id):
    if user_id not in user_data:
        user_data[user_id] = {"capital": 3000.0, "positions": [], "temp": {}}
    return user_data[user_id]

def calculate_position(capital, risk_pct, entry, direction, stop_price=None, stop_percent=None, leverage=10):
    """
    计算仓位详细信息，包含精确强平价格（考虑维持保证金率）
    返回 dict: quantity, notional, margin, leverage, liquidation_price, stop_loss, stop_distance, risk_amount
    """
    risk_amount = capital * (risk_pct / 100)
    
    # 确定止损价和止损距离
    if stop_price is not None:
        stop_loss = stop_price
        stop_distance = abs(entry - stop_price)
    elif stop_percent is not None:
        stop_distance = entry * (stop_percent / 100)
        if direction == 'long':
            stop_loss = entry - stop_distance
        else:
            stop_loss = entry + stop_distance
    else:
        return {"error": "需要止损价或止损%"}
    
    if stop_distance <= 0:
        return {"error": "止损距离无效"}
    
    quantity = risk_amount / stop_distance
    notional = quantity * entry
    margin = notional / leverage
    
    # 精确强平价格（考虑维持保证金率）
    if direction == 'long':
        # 做多强平 = 入场价 × (1 - (1 - 维持保证金率) / 杠杆)
        liquidation_price = entry * (1 - (1 - MAINTENANCE_MARGIN_RATE) / leverage)
    else:
        # 做空强平 = 入场价 × (1 + (1 - 维持保证金率) / 杠杆)
        liquidation_price = entry * (1 + (1 - MAINTENANCE_MARGIN_RATE) / leverage)
    
    return {
        "quantity": quantity,
        "notional": notional,
        "margin": margin,
        "leverage": leverage,
        "liquidation_price": liquidation_price,
        "stop_loss": stop_loss,
        "stop_distance": stop_distance,
        "risk_amount": risk_amount
    }

def get_total_notional(positions):
    return sum(p.get("notional", 0) for p in positions)

def get_total_leverage(capital, positions):
    if capital <= 0:
        return 0
    return get_total_notional(positions) / capital

# ========== 命令处理 ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **仓位管理机器人 v2 (精确强平版)**\n\n"
        "支持命令:\n"
        "/capital - 查看/设置本金\n"
        "/pnl +100 - 记录盈亏\n"
        "/add - 添加持仓\n"
        "/list - 查看持仓和总杠杆\n"
        "/remove - 移除持仓\n"
        "/clear - 清空所有持仓"
    )

async def capital_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    args = context.args
    if args:
        try:
            new_cap = float(args[0])
            if new_cap <= 0:
                await update.message.reply_text("本金必须大于0")
                return
            state["capital"] = new_cap
            await update.message.reply_text(f"✅ 本金已设置为 {new_cap} USDT")
        except:
            await update.message.reply_text("格式错误，例如: /capital 3500")
    else:
        await update.message.reply_text(f"💰 当前本金: {state['capital']} USDT\n"
                                        f"📊 当前总杠杆: {get_total_leverage(state['capital'], state['positions']):.2f}x")

async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    args = context.args
    if not args:
        await update.message.reply_text("请提供盈亏金额，例如: /pnl +50 或 /pnl -30")
        return
    try:
        change = float(args[0])
        new_capital = state["capital"] + change
        if new_capital <= 0:
            await update.message.reply_text("本金不能为负或零")
            return
        state["capital"] = new_capital
        await update.message.reply_text(f"✅ 本金已更新: {state['capital']:.2f} USDT\n"
                                        f"总杠杆变为: {get_total_leverage(state['capital'], state['positions']):.2f}x")
    except:
        await update.message.reply_text("格式错误，例如: /pnl +50")

async def add_position_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    state["temp"] = {"step": "symbol"}
    await update.message.reply_text("请输入仓位名称 (例: BTC):")

async def add_position_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    temp = state.get("temp", {})
    step = temp.get("step")
    text = update.message.text.strip()
    
    if not step:
        return
    
    if step == "symbol":
        temp["symbol"] = text.upper()
        temp["step"] = "direction"
        kb = [[InlineKeyboardButton("📈 做多", callback_data="dir_long"), InlineKeyboardButton("📉 做空", callback_data="dir_short")]]
        await update.message.reply_text("选择方向:", reply_markup=InlineKeyboardMarkup(kb))
    elif step == "entry":
        try:
            temp["entry"] = float(text)
            temp["step"] = "stop_type"
            kb = [[InlineKeyboardButton("止损价", callback_data="stop_price"), InlineKeyboardButton("止损%", callback_data="stop_percent")]]
            await update.message.reply_text("选择止损方式:", reply_markup=InlineKeyboardMarkup(kb))
        except:
            await update.message.reply_text("请输入数字")
    elif step == "stop_price":
        try:
            temp["stop_price"] = float(text)
            temp["step"] = "leverage"
            await update.message.reply_text("请输入合约杠杆倍数 (例: 10):")
        except:
            await update.message.reply_text("请输入数字")
    elif step == "stop_percent":
        try:
            temp["stop_percent"] = float(text)
            temp["step"] = "leverage"
            await update.message.reply_text("请输入合约杠杆倍数 (例: 10):")
        except:
            await update.message.reply_text("请输入数字")
    elif step == "leverage":
        try:
            leverage = float(text)
            if leverage <= 0:
                raise
            temp["leverage"] = leverage
            # 计算仓位
            capital = state["capital"]
            risk_pct = 1.0  # 固定1%风险
            entry = temp["entry"]
            direction = temp.get("direction", "long")
            if "stop_price" in temp:
                stop_price = temp["stop_price"]
                res = calculate_position(capital, risk_pct, entry, direction, stop_price=stop_price, leverage=leverage)
            else:
                stop_percent = temp["stop_percent"]
                res = calculate_position(capital, risk_pct, entry, direction, stop_percent=stop_percent, leverage=leverage)
            
            if "error" in res:
                await update.message.reply_text(f"计算错误: {res['error']}")
                state["temp"] = {}
                return
            
            # 检查总杠杆
            new_notional = res["notional"]
            total_notional = get_total_notional(state["positions"]) + new_notional
            new_total_lev = total_notional / capital
            if new_total_lev > 3.0:
                await update.message.reply_text(f"⚠️ 加仓后总杠杆将达 {new_total_lev:.2f}x，超过3倍上限！请减少仓位或先平掉部分持仓。")
                state["temp"] = {}
                return
            
            # 添加到持仓
            position = {
                "symbol": temp["symbol"],
                "direction": direction,
                "entry": entry,
                "quantity": res["quantity"],
                "notional": res["notional"],
                "leverage": leverage,
                "stop_loss": res["stop_loss"],
                "liquidation_price": res["liquidation_price"]
            }
            state["positions"].append(position)
            await update.message.reply_text(
                f"✅ 已添加持仓 {temp['symbol']} ({'做多' if direction=='long' else '做空'})\n"
                f"数量: {res['quantity']:.6f}\n"
                f"名义: {res['notional']:.2f} USDT\n"
                f"强平: {res['liquidation_price']:.2f}\n"
                f"当前总杠杆: {get_total_leverage(capital, state['positions']):.2f}x"
            )
            state["temp"] = {}
        except Exception as e:
            await update.message.reply_text(f"输入无效: {str(e)}")
            state["temp"] = {}

async def list_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    positions = state["positions"]
    capital = state["capital"]
    if not positions:
        await update.message.reply_text("当前无持仓")
        return
    msg = "📋 **当前持仓**\n\n"
    for i, p in enumerate(positions):
        msg += f"{i+1}. {p['symbol']} ({'做多' if p['direction']=='long' else '做空'})\n"
        msg += f"   数量: {p['quantity']:.6f} | 名义: {p['notional']:.0f} U\n"
        msg += f"   强平: {p['liquidation_price']:.2f}\n\n"
    total_lev = get_total_leverage(capital, positions)
    msg += f"💰 本金: {capital:.2f} U\n"
    msg += f"📊 总杠杆: {total_lev:.2f}x"
    await update.message.reply_text(msg)

async def remove_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    if not state["positions"]:
        await update.message.reply_text("无持仓可移除")
        return
    await update.message.reply_text("请回复要移除的持仓序号 (1,2,3...):")
    context.user_data["waiting_remove"] = True

async def handle_remove_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    if not context.user_data.get("waiting_remove"):
        return
    try:
        idx = int(update.message.text.strip()) - 1
        if 0 <= idx < len(state["positions"]):
            removed = state["positions"].pop(idx)
            await update.message.reply_text(f"已移除 {removed['symbol']} 持仓")
        else:
            await update.message.reply_text("序号无效")
    except:
        await update.message.reply_text("请输入数字序号")
    context.user_data["waiting_remove"] = False

async def clear_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    state["positions"] = []
    await update.message.reply_text("已清空所有持仓")

async def calc_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("请使用 /add 添加持仓时自动计算")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    state = get_user_state(user_id)
    temp = state.get("temp", {})
    
    if data == "dir_long":
        temp["direction"] = "long"
        temp["step"] = "entry"
        await query.edit_message_text("请输入入场价 (USDT):")
    elif data == "dir_short":
        temp["direction"] = "short"
        temp["step"] = "entry"
        await query.edit_message_text("请输入入场价 (USDT):")
    elif data == "stop_price":
        temp["step"] = "stop_price"
        await query.edit_message_text("请输入止损价 (USDT):")
    elif data == "stop_percent":
        temp["step"] = "stop_percent"
        await query.edit_message_text("请输入止损幅度 (%):")
    state["temp"] = temp

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("capital", capital_command))
    app.add_handler(CommandHandler("pnl", pnl_command))
    app.add_handler(CommandHandler("add", add_position_start))
    app.add_handler(CommandHandler("list", list_positions))
    app.add_handler(CommandHandler("remove", remove_position))
    app.add_handler(CommandHandler("clear", clear_positions))
    app.add_handler(CommandHandler("calc", calc_single))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_position_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_reply))
    app.add_handler(CallbackQueryHandler(button_callback))
    print("机器人启动...")
    app.run_polling()

if __name__ == "__main__":
    main()
