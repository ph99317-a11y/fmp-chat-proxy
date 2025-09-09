    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    # Erzwinge Deutsch & Value-Fokus
    prompt = ANALYZE_TEMPLATE.format(
        perspective="value",
        language="de",
        symbol=req.symbol.upper(),
        profile=profile,
        quote=quote,
        key_metrics=key_metrics,
        ratios=ratios,
        income=income,
        balance=balance,
        cashflow=cashflow,
        peers=peers,
        news=news_items[:5],
        as_of=as_of
    )

    try:
        client = get_client()
        result = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role":"system","content":"Du bist ein gründlicher Value-Investor. Liefere eine ausführliche, datenfundierte Deep-Dive-Analyse auf Deutsch. Keine Anlageberatung."},
                {"role":"user","content":prompt}
            ],
            temperature=0.2,
            max_tokens=2000  # mehr Platz für Deep-Dive
        )
        content = result.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")
