def hour_key(local_hour):
    return f"{local_hour.date().isoformat()}:{local_hour.hour:02d}:{local_hour.fold}"
