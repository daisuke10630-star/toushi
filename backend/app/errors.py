"""データ取得元に依存しない共通例外。

main.py はこの例外だけを見れば、取得元が J-Quants でも yfinance でも
同じようにエラーハンドリングできます。
"""


class DataSourceAuthError(Exception):
    """APIキー未設定・認証失敗など、利用者の設定で解決すべきエラー"""


class DataSourceFetchError(Exception):
    """通信エラー・データ不在など、取得そのものに失敗したエラー"""
