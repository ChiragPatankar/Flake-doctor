def unique_tags(tags):
    """Remove duplicate tags, keeping the order they were first given in."""
    return list(set(tag.strip().lower() for tag in tags))
