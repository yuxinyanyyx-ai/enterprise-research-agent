"""Offline lexical recall, bounded query expansion and rank fusion."""
import math
import re
from collections import Counter


# Search vocabulary, never equivalence rules for interpreting meeting decisions.
QUERY_ALIASES = (
    ("临床试验", "clinical trial"), ("延期", "extension delay"),
    ("申报", "submission filing"), ("注册", "registration"),
    ("方案", "protocol"), ("适应症", "indication"),
    ("会议", "meeting"), ("决议", "decision"), ("批准", "approval"),
)


def recall_queries(query, suggestions=None):
    """Keep the original intact; add at most two bounded recall-only variants."""
    original = query.strip()
    candidates = []
    for value in suggestions or []:
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 256:
            candidates.append(value.strip())
    aliases = []
    for chinese, english in QUERY_ALIASES:
        if chinese in original:
            aliases.append(english)
        elif any(re.search(r"\b" + re.escape(word) + r"\b", original, re.I) for word in english.split()):
            aliases.append(chinese)
    if aliases:
        candidates.append(" ".join(aliases))
    result, seen = [original], {" ".join(original.casefold().split())}
    for candidate in candidates:
        key = " ".join(candidate.casefold().split())
        if key in seen:
            continue
        seen.add(key)
        # Appending rather than replacing retains identifiers, negation and conditions.
        result.append(original + "\n" + candidate)
        if len(result) == 3:
            break
    return result


def tokens(text):
    text = text.casefold()
    words = re.findall(r"[a-z0-9]+(?:[-_./][a-z0-9]+)*", text)
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.extend(run[i:i+2] for i in range(max(1, len(run)-1)))
    return words


def lexical_scores(query, chunks):
    return lexical_scores_many([query], chunks)[0]


def lexical_scores_many(queries, chunks):
    term_sets = [set(tokens(query)) for query in queries]
    documents = [Counter(tokens(c["source_ref"] + " " + c["text"])) for c in chunks]
    lengths = [sum(d.values()) for d in documents]
    average = sum(lengths) / max(1, len(lengths)) or 1
    frequency = {term: sum(term in d for d in documents) for term in set().union(*term_sets)}
    results = []
    for terms in term_sets:
        scores = []
        for document, length in zip(documents, lengths):
            score = 0.
            for term in terms:
                count = document.get(term, 0)
                if count:
                    idf = math.log(1 + (len(documents) - frequency[term] + .5) / (frequency[term] + .5))
                    score += idf * count * 2.2 / (count + 1.2 * (.25 + .75 * length / average))
            scores.append(score)
        results.append(scores)
    return results


def fuse(vector_order, lexical_order, chunks, limit, expanded_orders=()):
    scores = {}
    rankings = [(vector_order, 1.), (lexical_order, 1.)] + [(r, .3) for r in expanded_orders]
    for ranking, weight in rankings:
        for rank, index in enumerate(ranking, 1):
            scores[index] = scores.get(index, 0) + weight / (60 + rank)
    order = sorted(scores, key=lambda i: (-scores[i], i))
    # Reserve a small budget for variant-only matches before the original-query rerank.
    protected = list(dict.fromkeys(i for ranking, _ in rankings for i in ranking[:2]))[:limit]
    # Reserve room for both evidence and topics in reranking.
    selected = protected if expanded_orders else []
    for is_topic in (False, True):
        count = sum((chunks[i].get("method") == "topic") == is_topic for i in selected)
        selected.extend([i for i in order if i not in selected and (chunks[i].get("method") == "topic") == is_topic][:max(0, min(limit // 2 - count, limit - len(selected)))])
    selected = set(selected + [i for i in order if i not in selected][:max(0, limit - len(selected))])
    return [i for i in order if i in selected]


def diverse(rows, chunks, count):
    selected, deferred, seen, per_file = [], [], set(), {}
    for index, score in rows:
        chunk = chunks[index]
        key = (chunk["source_ref"], " ".join(chunk["text"].casefold().split()))
        if key in seen:
            continue
        seen.add(key)
        ref = chunk["source_ref"]
        if per_file.get(ref, 0) >= 2:
            deferred.append((index, score))
        else:
            selected.append((index, score))
            per_file[ref] = per_file.get(ref, 0) + 1
    return (selected + deferred)[:count]


def excerpt(text, limit, query=""):
    if len(text) <= limit:
        return text
    marker = "\n[中间内容因上下文预算省略；请核对原文件]\n"
    if query and limit > 300:
        available = limit - len(marker) * 2
        size = available // 3
        for term in sorted(set(tokens(query)), key=len, reverse=True):
            position = text.casefold().find(term)
            if size < position < len(text) - size:
                start = max(size, min(position - size // 2, len(text) - size * 2))
                return text[:size] + marker + text[start:start+size] + marker + text[-size:]
    size = max(0, (limit - len(marker)) // 2)
    return text[:size] + marker + text[-size:] if size else marker[:limit]


def neighbors(item, chunks):
    """Short decisions often need the adjacent paragraph's subject or heading."""
    match = re.fullmatch(r"Paragraph (\d+)", item.get("loc", ""))
    if not match or item.get("method") != "text" or len(item["text"]) >= 400:
        return []
    wanted = {f"Paragraph {int(match.group(1))-1}", f"Paragraph {int(match.group(1))+1}"}
    return [{**c, "text": "[邻近段落，仅补充上下文，未独立重排]\n" + excerpt(c["text"], 1000)}
            for c in chunks if c["source_ref"] == item["source_ref"] and c.get("method") == "text" and c.get("loc") in wanted][:2]
