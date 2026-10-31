SELECT coalesce(
           (SELECT max(arrived_at) FROM source_orders
            WHERE arrived_at > (SELECT last_watermark FROM load_state)),
           (SELECT last_watermark FROM load_state)
       ) AS next_watermark;
