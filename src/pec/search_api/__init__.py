__all__ = ["search_topics"]


def __getattr__(name: str):
	if name == "search_topics":
		from src.pec.search_api.engine import search_topics

		return search_topics
	raise AttributeError(name)
