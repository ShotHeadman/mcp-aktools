"""SQLite 存储模块。

该模块实现 SQLite 数据存储类，用于：
- 回测结果存储和检索
- 交易记录存储和检索
- 自定义指标存储和检索
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from ..exceptions import StorageError

if TYPE_CHECKING:
    from ..backtest.results import BacktestResult
    from ..models.indicator import CustomIndicator
    from ..models.trade import TradeRecord


class SQLiteStorage:
    """SQLite 数据存储类。
    
    提供回测数据的持久化存储功能，包括：
    - 回测结果的保存和检索
    - 交易记录的保存和检索
    - 自定义指标的保存和检索
    
    Attributes:
        db_path: 数据库文件路径
    """
    
    def __init__(self, db_path: str = "data/backtest.db"):
        """初始化 SQLiteStorage。
        
        Args:
            db_path: 数据库文件路径，默认为 data/backtest.db
        """
        self.db_path = db_path
        # 确保数据库目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # 初始化数据库表
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接。
        
        Returns:
            sqlite3.Connection: 数据库连接对象
            
        Raises:
            StorageError: 连接失败时抛出
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row  # 支持按列名访问
            return conn
        except sqlite3.Error as e:
            raise StorageError("connect", f"数据库连接失败: {e}") from e

    def _init_db(self) -> None:
        """初始化数据库表结构。
        
        创建以下表：
        - backtest_results: 回测结果表
        - trade_records: 交易记录表
        - custom_indicators: 自定义指标表
        
        同时创建必要的索引以提高查询效率。
        
        Raises:
            StorageError: 表创建失败时抛出
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 创建回测结果表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS backtest_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT,
                    stock_code TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    initial_capital REAL NOT NULL,
                    final_capital REAL NOT NULL,
                    benchmark_return REAL,
                    strategy_return REAL,
                    max_drawdown REAL,
                    volatility REAL,
                    sharpe_ratio REAL,
                    win_rate REAL,
                    profit_factor REAL,
                    total_trades INTEGER,
                    config_json TEXT,
                    equity_curve_json TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            
            # 创建交易记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    result_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    commission REAL NOT NULL,
                    cash_after REAL NOT NULL,
                    position_after INTEGER NOT NULL,
                    FOREIGN KEY (result_id) REFERENCES backtest_results(id)
                )
            """)
            
            # 创建自定义指标表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS custom_indicators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    function_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # 创建索引以提高查询效率
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_backtest_results_stock 
                ON backtest_results (stock_code)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_backtest_results_date 
                ON backtest_results (created_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_backtest_results_start_date 
                ON backtest_results (start_date)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_backtest_results_end_date 
                ON backtest_results (end_date)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trade_records_result 
                ON trade_records (result_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_custom_indicators_name 
                ON custom_indicators (name)
            """)
            
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            raise StorageError("init_db", f"数据库初始化失败: {e}") from e

    # ========================================================================
    # 回测结果存储方法
    # ========================================================================
    
    def save_backtest_result(self, result: "BacktestResult", 
                             strategy_name: Optional[str] = None) -> int:
        """保存回测结果到数据库。
        
        Args:
            result: 回测结果对象
            strategy_name: 策略名称（可选）
            
        Returns:
            int: 保存的回测结果 ID
            
        Raises:
            StorageError: 保存失败时抛出
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 获取当前时间
            created_at = datetime.now().isoformat()
            
            # 序列化权益曲线
            equity_curve_json = json.dumps(result.equity_curve)
            
            # 插入回测结果
            cursor.execute("""
                INSERT INTO backtest_results (
                    strategy_name, stock_code, start_date, end_date, frequency,
                    initial_capital, final_capital, benchmark_return, strategy_return,
                    max_drawdown, volatility, sharpe_ratio, win_rate, profit_factor,
                    total_trades, config_json, equity_curve_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                strategy_name,
                result.config.stock_code,
                result.config.start_date,
                result.config.end_date,
                result.config.frequency,
                result.config.initial_capital,
                result.final_capital,
                result.benchmark_return,
                result.strategy_return,
                result.max_drawdown,
                result.volatility,
                result.sharpe_ratio,
                result.win_rate,
                result.profit_factor,
                result.total_trades,
                result.config.to_json(),
                equity_curve_json,
                created_at,
            ))
            
            result_id = cursor.lastrowid
            
            # 保存交易记录
            if result.trades:
                self._save_trade_records_internal(cursor, result_id, result.trades)
            
            conn.commit()
            conn.close()
            
            return result_id
        except sqlite3.Error as e:
            raise StorageError("save_backtest_result", f"保存回测结果失败: {e}") from e
    
    def get_backtest_result(self, result_id: int) -> Optional["BacktestResult"]:
        """根据 ID 获取回测结果。
        
        Args:
            result_id: 回测结果 ID
            
        Returns:
            BacktestResult 对象，如果不存在则返回 None
            
        Raises:
            StorageError: 查询失败时抛出
        """
        # 延迟导入避免循环依赖
        from ..backtest.config import BacktestConfig
        from ..backtest.results import BacktestResult
        from ..models.trade import TradeRecord
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 查询回测结果
            cursor.execute("""
                SELECT * FROM backtest_results WHERE id = ?
            """, (result_id,))
            
            row = cursor.fetchone()
            if row is None:
                conn.close()
                return None
            
            # 反序列化配置
            config = BacktestConfig.from_json(row["config_json"])
            
            # 反序列化权益曲线
            equity_curve = json.loads(row["equity_curve_json"]) if row["equity_curve_json"] else []
            
            # 获取交易记录
            trades = self.get_trade_records(result_id)
            
            conn.close()
            
            return BacktestResult(
                config=config,
                trades=trades,
                final_capital=row["final_capital"],
                benchmark_return=row["benchmark_return"] or 0.0,
                strategy_return=row["strategy_return"] or 0.0,
                max_drawdown=row["max_drawdown"] or 0.0,
                volatility=row["volatility"] or 0.0,
                sharpe_ratio=row["sharpe_ratio"] or 0.0,
                win_rate=row["win_rate"] or 0.0,
                profit_factor=row["profit_factor"] or 0.0,
                total_trades=row["total_trades"] or 0,
                equity_curve=equity_curve,
            )
        except sqlite3.Error as e:
            raise StorageError("get_backtest_result", f"获取回测结果失败: {e}") from e
    
    def list_backtest_results(
        self,
        stock_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        strategy_name: Optional[str] = None,
        limit: int = 50
    ) -> List[dict]:
        """列出回测结果（带过滤功能）。
        
        Args:
            stock_code: 按股票代码过滤
            start_date: 按开始日期过滤（回测开始日期 >= start_date）
            end_date: 按结束日期过滤（回测结束日期 <= end_date）
            strategy_name: 按策略名称过滤
            limit: 返回结果数量限制，默认 50
            
        Returns:
            回测结果摘要列表（不包含完整交易记录）
            
        Raises:
            StorageError: 查询失败时抛出
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 构建查询条件
            conditions = []
            params = []
            
            if stock_code:
                conditions.append("stock_code = ?")
                params.append(stock_code)
            
            if start_date:
                conditions.append("start_date >= ?")
                params.append(start_date)
            
            if end_date:
                conditions.append("end_date <= ?")
                params.append(end_date)
            
            if strategy_name:
                conditions.append("strategy_name = ?")
                params.append(strategy_name)
            
            # 构建 SQL 查询
            sql = """
                SELECT id, strategy_name, stock_code, start_date, end_date,
                       frequency, initial_capital, final_capital, benchmark_return,
                       strategy_return, max_drawdown, volatility, sharpe_ratio,
                       win_rate, profit_factor, total_trades, created_at
                FROM backtest_results
            """
            
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            conn.close()
            
            # 转换为字典列表
            results = []
            for row in rows:
                results.append({
                    "id": row["id"],
                    "strategy_name": row["strategy_name"],
                    "stock_code": row["stock_code"],
                    "start_date": row["start_date"],
                    "end_date": row["end_date"],
                    "frequency": row["frequency"],
                    "initial_capital": row["initial_capital"],
                    "final_capital": row["final_capital"],
                    "benchmark_return": row["benchmark_return"],
                    "strategy_return": row["strategy_return"],
                    "max_drawdown": row["max_drawdown"],
                    "volatility": row["volatility"],
                    "sharpe_ratio": row["sharpe_ratio"],
                    "win_rate": row["win_rate"],
                    "profit_factor": row["profit_factor"],
                    "total_trades": row["total_trades"],
                    "created_at": row["created_at"],
                })
            
            return results
        except sqlite3.Error as e:
            raise StorageError("list_backtest_results", f"列出回测结果失败: {e}") from e

    # ========================================================================
    # 交易记录存储方法
    # ========================================================================
    
    def _save_trade_records_internal(
        self, 
        cursor: sqlite3.Cursor, 
        result_id: int, 
        trades: List["TradeRecord"]
    ) -> int:
        """内部方法：保存交易记录（使用已有的游标）。
        
        Args:
            cursor: 数据库游标
            result_id: 关联的回测结果 ID
            trades: 交易记录列表
            
        Returns:
            int: 保存的交易记录数量
        """
        for trade in trades:
            cursor.execute("""
                INSERT INTO trade_records (
                    result_id, timestamp, action, quantity, price,
                    commission, cash_after, position_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result_id,
                trade.timestamp,
                trade.action,
                trade.quantity,
                trade.price,
                trade.commission,
                trade.cash_after,
                trade.position_after,
            ))
        
        return len(trades)
    
    def save_trade_records(self, result_id: int, trades: List["TradeRecord"]) -> int:
        """保存交易记录到数据库。
        
        Args:
            result_id: 关联的回测结果 ID
            trades: 交易记录列表
            
        Returns:
            int: 保存的交易记录数量
            
        Raises:
            StorageError: 保存失败时抛出
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            count = self._save_trade_records_internal(cursor, result_id, trades)
            
            conn.commit()
            conn.close()
            
            return count
        except sqlite3.Error as e:
            raise StorageError("save_trade_records", f"保存交易记录失败: {e}") from e
    
    def get_trade_records(self, result_id: int) -> List["TradeRecord"]:
        """获取指定回测结果的交易记录。
        
        Args:
            result_id: 回测结果 ID
            
        Returns:
            交易记录列表
            
        Raises:
            StorageError: 查询失败时抛出
        """
        # 延迟导入避免循环依赖
        from ..models.trade import TradeRecord
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT timestamp, action, quantity, price, commission,
                       cash_after, position_after
                FROM trade_records
                WHERE result_id = ?
                ORDER BY id ASC
            """, (result_id,))
            
            rows = cursor.fetchall()
            conn.close()
            
            trades = []
            for row in rows:
                trades.append(TradeRecord(
                    timestamp=row["timestamp"],
                    action=row["action"],
                    quantity=row["quantity"],
                    price=row["price"],
                    commission=row["commission"],
                    cash_after=row["cash_after"],
                    position_after=row["position_after"],
                ))
            
            return trades
        except sqlite3.Error as e:
            raise StorageError("get_trade_records", f"获取交易记录失败: {e}") from e

    # ========================================================================
    # 自定义指标存储方法
    # ========================================================================
    
    def save_custom_indicator(self, indicator: "CustomIndicator") -> None:
        """保存自定义指标到数据库。
        
        如果指标名称已存在，则更新现有记录。
        
        Args:
            indicator: 自定义指标对象
            
        Raises:
            StorageError: 保存失败时抛出
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            now = datetime.now().isoformat()
            
            # 检查是否已存在
            cursor.execute("""
                SELECT id FROM custom_indicators WHERE name = ?
            """, (indicator.name,))
            
            existing = cursor.fetchone()
            
            if existing:
                # 更新现有记录
                cursor.execute("""
                    UPDATE custom_indicators
                    SET description = ?, function_code = ?, updated_at = ?
                    WHERE name = ?
                """, (
                    indicator.description,
                    indicator.function_code,
                    now,
                    indicator.name,
                ))
            else:
                # 插入新记录
                cursor.execute("""
                    INSERT INTO custom_indicators (
                        name, description, function_code, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    indicator.name,
                    indicator.description,
                    indicator.function_code,
                    now,
                    now,
                ))
            
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            raise StorageError("save_custom_indicator", f"保存自定义指标失败: {e}") from e
    
    def get_custom_indicator(self, name: str) -> Optional["CustomIndicator"]:
        """根据名称获取自定义指标。
        
        Args:
            name: 指标名称
            
        Returns:
            CustomIndicator 对象，如果不存在则返回 None
            
        Raises:
            StorageError: 查询失败时抛出
        """
        # 延迟导入避免循环依赖
        from ..models.indicator import CustomIndicator
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT name, description, function_code
                FROM custom_indicators
                WHERE name = ?
            """, (name,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row is None:
                return None
            
            return CustomIndicator(
                name=row["name"],
                description=row["description"] or "",
                function_code=row["function_code"],
            )
        except sqlite3.Error as e:
            raise StorageError("get_custom_indicator", f"获取自定义指标失败: {e}") from e
    
    def list_custom_indicators(self) -> List["CustomIndicator"]:
        """列出所有自定义指标。
        
        Returns:
            自定义指标列表
            
        Raises:
            StorageError: 查询失败时抛出
        """
        # 延迟导入避免循环依赖
        from ..models.indicator import CustomIndicator
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT name, description, function_code
                FROM custom_indicators
                ORDER BY name ASC
            """)
            
            rows = cursor.fetchall()
            conn.close()
            
            indicators = []
            for row in rows:
                indicators.append(CustomIndicator(
                    name=row["name"],
                    description=row["description"] or "",
                    function_code=row["function_code"],
                ))
            
            return indicators
        except sqlite3.Error as e:
            raise StorageError("list_custom_indicators", f"列出自定义指标失败: {e}") from e
    
    def delete_custom_indicator(self, name: str) -> bool:
        """删除自定义指标。
        
        Args:
            name: 指标名称
            
        Returns:
            bool: 是否成功删除（如果指标不存在返回 False）
            
        Raises:
            StorageError: 删除失败时抛出
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM custom_indicators WHERE name = ?
            """, (name,))
            
            deleted = cursor.rowcount > 0
            
            conn.commit()
            conn.close()
            
            return deleted
        except sqlite3.Error as e:
            raise StorageError("delete_custom_indicator", f"删除自定义指标失败: {e}") from e
    
    def delete_backtest_result(self, result_id: int) -> bool:
        """删除回测结果及其关联的交易记录。
        
        Args:
            result_id: 回测结果 ID
            
        Returns:
            bool: 是否成功删除（如果结果不存在返回 False）
            
        Raises:
            StorageError: 删除失败时抛出
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 先删除关联的交易记录
            cursor.execute("""
                DELETE FROM trade_records WHERE result_id = ?
            """, (result_id,))
            
            # 再删除回测结果
            cursor.execute("""
                DELETE FROM backtest_results WHERE id = ?
            """, (result_id,))
            
            deleted = cursor.rowcount > 0
            
            conn.commit()
            conn.close()
            
            return deleted
        except sqlite3.Error as e:
            raise StorageError("delete_backtest_result", f"删除回测结果失败: {e}") from e
