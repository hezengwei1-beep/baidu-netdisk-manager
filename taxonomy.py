"""分类体系定义模块"""

from dataclasses import dataclass, field
from rich.console import Console
from rich.tree import Tree

console = Console()


@dataclass
class TaxonomyNode:
    """分类节点"""
    name: str
    path: str  # 网盘完整路径，如 /健康运动/健身训练
    keywords: list[str] = field(default_factory=list)
    children: list["TaxonomyNode"] = field(default_factory=list)
    frozen: bool = False  # 冻结目录不参与迁移

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0


class Taxonomy:
    """分类体系"""

    def __init__(self, roots: list[TaxonomyNode]):
        self.roots = roots
        self._index: dict[str, TaxonomyNode] = {}
        self._build_index()

    def _build_index(self):
        """构建路径索引"""
        def _walk(node: TaxonomyNode):
            self._index[node.path] = node
            for child in node.children:
                _walk(child)
        for root in self.roots:
            _walk(root)

    def all_paths(self) -> list[str]:
        """返回所有分类路径"""
        return list(self._index.keys())

    def all_leaf_paths(self) -> list[str]:
        """返回所有叶子节点路径"""
        return [path for path, node in self._index.items() if node.is_leaf]

    def find_node(self, path: str) -> TaxonomyNode | None:
        """按路径查找节点"""
        return self._index.get(path)

    def validate(self) -> list[str]:
        """验证分类体系，返回错误列表"""
        errors = []
        paths = self.all_paths()
        # 检查路径唯一性
        seen = set()
        for p in paths:
            if p in seen:
                errors.append(f"重复路径: {p}")
            seen.add(p)
        # 检查根节点不为空
        if not self.roots:
            errors.append("分类体系为空")
        return errors


def load_taxonomy(config: dict) -> Taxonomy:
    """从 config 加载分类体系"""
    taxonomy_config = config.get("taxonomy", {})
    categories = taxonomy_config.get("categories", [])

    roots = []
    for cat in categories:
        root = _build_node(cat, parent_path="")
        roots.append(root)

    return Taxonomy(roots)


def _build_node(node_config: dict, parent_path: str) -> TaxonomyNode:
    """递归构建分类节点"""
    name = node_config["name"]
    path = f"{parent_path}/{name}"
    keywords = node_config.get("keywords", [])
    frozen = node_config.get("frozen", False)
    children_config = node_config.get("children", [])

    children = [_build_node(c, path) for c in children_config]

    return TaxonomyNode(
        name=name,
        path=path,
        keywords=keywords,
        children=children,
        frozen=frozen,
    )


def print_taxonomy_tree(taxonomy: Taxonomy):
    """用 Rich Tree 展示分类树"""
    tree = Tree("[bold]百度网盘知识分类体系[/bold]", guide_style="dim")

    for root in taxonomy.roots:
        _add_to_tree(tree, root)

    console.print(tree)


def _add_to_tree(parent_tree: Tree, node: TaxonomyNode):
    """递归添加节点到 Rich Tree"""
    label = node.name
    if node.frozen:
        label += " [dim](冻结)[/dim]"
    if node.keywords:
        kw_str = ", ".join(node.keywords[:5])
        if len(node.keywords) > 5:
            kw_str += f" +{len(node.keywords) - 5}"
        label += f" [dim cyan]({kw_str})[/dim cyan]"

    branch = parent_tree.add(label)
    for child in node.children:
        _add_to_tree(branch, child)
