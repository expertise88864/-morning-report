"""Distinguish hospital news from property headlines using hospitals as landmarks."""
import re

HOSPITAL_ENTITY = (r"彰基|彰化基督教(?:兒童醫院|醫院|醫療財團法人)|(?:鹿港|二林|員林|雲林)基督教醫院|"
                   r"中國附醫|中國醫附醫|中國醫藥大學|中國醫大|中醫大|中國醫(?!療|學|師|生|界|藥|院|保|美)")
_LANDMARK = re.compile(rf"(?:{HOSPITAL_ENTITY})(?:附醫|附設醫院)?(?:商圈|旁|附近|周邊|鄰近)")
_PROPERTY = re.compile(r"建案|預售屋|房價|房市|住宅|推案|建商|每坪|成交價")


def hospital_news_title(title: str) -> bool:
    """A hospital named only as a property landmark is not the news actor."""
    return bool(re.search(HOSPITAL_ENTITY, title) and
                not (_LANDMARK.search(title) and _PROPERTY.search(title)))
